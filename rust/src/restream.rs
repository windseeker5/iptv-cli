//! Restream one live channel as HLS for the family.
//!
//! Two ways, set by `restream_via` in kdtv.toml:
//! - "nginx": FFmpeg pushes RTMP to the NGINX-RTMP container, which writes and
//!   serves the HLS files (home, today);
//! - "kdtv": FFmpeg writes the HLS files itself and kdtv's web server (`web.rs`)
//!   serves them, same URLs, same viewer log (travel, a Pi, and home once NGINX goes).
//!
//! The restream runs as the systemd user unit `kd-restream`, so it survives kdtv
//! closing. That unit is kdtv itself (`kdtv restream-worker <id>`): it starts FFmpeg,
//! writes `data/.restream_active.json` (read by OMT and JP) and keeps a redacted
//! FFmpeg log in `data/logs/restream_*.log`.

use crate::config::{Config, RestreamVia};
use crate::db::{Channel, Db};
use crate::systemd;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const UNIT: &str = "kd-restream";
const NGINX_CONTAINER: &str = "iptv-nginx-rtmp";
const NGINX_SERVICE: &str = "nginx-rtmp";

#[derive(Serialize, Deserialize, Default)]
pub struct Active {
    pub stream_key: String,
    pub channel_name: String,
    pub pid: u32,
    pub started_at: String,
    #[serde(default)]
    pub log_path: Option<String>,
}

#[derive(Serialize)]
pub struct Started {
    pub channel_name: String,
    pub hls_url: String,
    pub watch_url: String,
    pub replaced: Option<String>,
}

fn meta_file(data_dir: &Path) -> PathBuf {
    data_dir.join(".restream_active.json")
}

/// The restream running right now, if its FFmpeg is still alive.
pub fn active(data_dir: &Path) -> Option<Active> {
    let meta: Active = serde_json::from_str(&std::fs::read_to_string(meta_file(data_dir)).ok()?).ok()?;
    Path::new(&format!("/proc/{}", meta.pid)).exists().then_some(meta)
}

pub fn start(cfg: &Config, channel: &Channel, replace: bool) -> Result<Started> {
    let current = active(&cfg.data_dir);
    if let Some(a) = &current
        && !replace
    {
        bail!("already restreaming {}; replace it to switch channels", a.channel_name);
    }
    if cfg.restream_via == RestreamVia::Nginx {
        ensure_nginx(cfg)?;
    }
    let replaced = current.map(|a| a.channel_name);
    if replaced.is_some() || systemd::is_active(UNIT) {
        stop(cfg)?;
    }

    let exe = std::env::current_exe().context("finding the kd program")?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.extend(["restream-worker".to_string(), channel.stream_id.to_string()]);
    systemd::run(systemd::Run {
        unit: UNIT,
        description: &format!("KD restream: {}", channel.name),
        working_dir: &cfg.cli_dir,
        max_seconds: None,
        command,
    })?;

    // Give FFmpeg a moment: a bad channel or a busy connection fails right away.
    for _ in 0..20 {
        std::thread::sleep(std::time::Duration::from_millis(250));
        if active(&cfg.data_dir).is_some() {
            break;
        }
    }
    std::thread::sleep(std::time::Duration::from_millis(1500));
    if !systemd::is_active(UNIT) || active(&cfg.data_dir).is_none() {
        bail!("the restream stopped right away; see the latest data/logs/restream_*.log");
    }
    crate::log::event(cfg, "restream.start", json!({ "stream_id": channel.stream_id, "channel": channel.name, "replaced": replaced }));
    let port = match cfg.restream_via {
        RestreamVia::Nginx => cfg.env("NGINX_HTTP_PORT").unwrap_or_else(|| "8080".into()),
        RestreamVia::Kdtv => cfg.web_port.to_string(),
    };
    // No public page configured (on the road): point at this machine on the Wi-Fi.
    let watch_url = if cfg.watch_url.is_empty() { crate::web::lan_urls(cfg).into_iter().next().unwrap_or_default() } else { cfg.watch_url.clone() };
    Ok(Started { channel_name: channel.name.clone(), hls_url: format!("http://localhost:{port}/hls/{}.m3u8", stream_key(&channel.name)), watch_url, replaced })
}

/// Stop the restream, whether kd or the old Python side started it.
pub fn stop(cfg: &Config) -> Result<Option<String>> {
    let current = active(&cfg.data_dir);
    if systemd::is_active(UNIT) {
        systemd::systemctl(&["stop", UNIT])?;
    }
    if let Some(a) = &current
        && Path::new(&format!("/proc/{}", a.pid)).exists()
    {
        let _ = Command::new("kill").arg(a.pid.to_string()).status();
    }
    if let Some(a) = &current
        && cfg.restream_via == RestreamVia::Kdtv
    {
        clear_hls(cfg, &a.stream_key);
    }
    let _ = std::fs::remove_file(meta_file(&cfg.data_dir));
    let name = current.map(|a| a.channel_name);
    crate::log::event(cfg, "restream.stop", json!({ "channel": name }));
    Ok(name)
}

/// Start the NGINX-RTMP container if it isn't running: the tool starts what it needs.
fn ensure_nginx(cfg: &Config) -> Result<()> {
    let up = Command::new("docker")
        .args(["ps", "--filter", &format!("name=^{NGINX_CONTAINER}$"), "--format", "{{.Status}}"])
        .output()
        .is_ok_and(|o| String::from_utf8_lossy(&o.stdout).starts_with("Up"));
    if up {
        return Ok(());
    }
    let out = Command::new("docker")
        .args(["compose", "up", "-d", NGINX_SERVICE])
        .current_dir(&cfg.compose_dir)
        .output()
        .context("running docker compose")?;
    if !out.status.success() {
        bail!("could not start the restream server: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    crate::log::event(cfg, "docker.start", json!({ "service": NGINX_SERVICE }));
    Ok(())
}

/// `kd restream-worker <id>`: runs inside the `kd-restream` unit until FFmpeg ends.
pub fn worker(cfg: &Config, stream_id: i64) -> Result<()> {
    let channel = Db::open(&cfg.data_dir)?.channel(stream_id)?.with_context(|| format!("no live channel {stream_id}"))?;
    let key = stream_key(&channel.name);
    let source = cfg.provider.stream_url(stream_id);
    let rtmp_port = cfg.env("NGINX_RTMP_PORT").unwrap_or_else(|| "1935".into());
    let logs = cfg.data_dir.join("logs");
    std::fs::create_dir_all(&logs)?;
    let log_path = logs.join(format!("restream_{}.log", chrono::Local::now().format("%Y%m%d_%H%M%S")));

    let mut ffmpeg = Command::new("ffmpeg");
    ffmpeg
        .args(["-nostdin", "-reconnect", "1", "-reconnect_at_eof", "1", "-reconnect_streamed", "1"])
        .args(["-reconnect_delay_max", "30", "-fflags", "+genpts+discardcorrupt", "-rtbufsize", "15M"])
        .args(["-analyzeduration", "10M", "-probesize", "10M", "-i", &source])
        .args(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-max_muxing_queue_size", "9999"]);
    match cfg.restream_via {
        RestreamVia::Nginx => {
            ffmpeg.args(["-f", "flv", &format!("rtmp://localhost:{rtmp_port}/live/{key}")]);
        }
        RestreamVia::Kdtv => {
            // The same 3 s pieces and one-minute playlist NGINX made.
            let dir = cfg.hls_dir();
            std::fs::create_dir_all(&dir)?;
            clear_hls(cfg, &key);
            ffmpeg
                .args(["-f", "hls", "-hls_time", "3", "-hls_list_size", "20", "-hls_flags", "delete_segments+independent_segments"])
                .arg("-hls_segment_filename")
                .arg(dir.join(format!("{key}-%d.ts")))
                .arg(dir.join(format!("{key}.m3u8")));
            // Serve it, unless `kdtv web` (or something else) already answers on the port.
            if !crate::web::port_in_use(cfg) {
                let listener = crate::web::bind(cfg)?;
                let web_cfg = cfg.clone();
                std::thread::spawn(move || crate::web::serve_on(&web_cfg, listener));
            }
        }
    }
    let mut child = ffmpeg
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .context("starting ffmpeg")?;

    let meta = Active {
        stream_key: key,
        channel_name: channel.name.clone(),
        pid: child.id(),
        started_at: chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
        log_path: Some(log_path.display().to_string()),
    };
    std::fs::write(meta_file(&cfg.data_dir), serde_json::to_string(&meta)?)?;

    let mut log = std::fs::File::create(&log_path)?;
    if let Some(stderr) = child.stderr.take() {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = writeln!(log, "{}", cfg.provider.redact(&line.replace(&source, "<provider-url>")));
        }
    }
    let status = child.wait()?;
    if active(&cfg.data_dir).is_none_or(|a| a.pid == meta.pid) {
        let _ = std::fs::remove_file(meta_file(&cfg.data_dir));
        if cfg.restream_via == RestreamVia::Kdtv {
            clear_hls(cfg, &meta.stream_key);
        }
    }
    crate::log::event(cfg, "restream.ended", json!({ "channel": channel.name, "code": status.code() }));
    if !status.success() {
        bail!("ffmpeg ended with {status}");
    }
    Ok(())
}

/// Remove a stream's playlist and pieces, so players don't loop an old one.
fn clear_hls(cfg: &Config, key: &str) {
    let Ok(entries) = std::fs::read_dir(cfg.hls_dir()) else { return };
    for e in entries.flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if name == format!("{key}.m3u8") || (name.starts_with(&format!("{key}-")) && name.ends_with(".ts")) {
            let _ = std::fs::remove_file(e.path());
        }
    }
}

/// "CA FR: TVA SPORTS HD" → "ca_fr_tva_sports_hd", like `restream.generate_stream_key`.
pub fn stream_key(name: &str) -> String {
    let key: String = name
        .replace(' ', "_")
        .to_lowercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-'))
        .collect();
    if key.is_empty() { "stream".into() } else { key }
}
