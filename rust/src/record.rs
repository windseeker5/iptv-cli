//! Record now, and stop a recording.
//!
//! "Now" goes through the same proven recorder the timers use
//! (`scripts/record_scheduled.py`: FFmpeg with retries, run diary, Jellyfin
//! post-processing, Discord), started as a transient systemd unit `kdcli-now-*`.

use crate::config::Config;
use crate::systemd;
use anyhow::{bail, Result};
use serde::Serialize;
use serde_json::json;
use std::path::{Path, PathBuf};

/// Units that are recordings: scheduled, started now, the NOOVO jobs, and the old TUI's.
pub const UNIT_PATTERNS: &[&str] =
    &["kdcli-rec-*.service", "kdcli-now-*.service", "iptv-noovo-record*.service", "iptv-record-*.service"];

pub const MAX_MINUTES: i64 = 6 * 60;

pub struct Request<'a> {
    pub stream_id: i64,
    pub channel_name: &'a str,
    pub minutes: i64,
    /// Set: remux into Jellyfin as this show. Unset: keep the .ts and ping Discord.
    pub show_name: Option<&'a str>,
}

#[derive(Serialize)]
pub struct Started {
    pub unit: String,
    pub output: String,
    pub warning: Option<String>,
}

pub fn record_now(cfg: &Config, req: &Request) -> Result<Started> {
    if !(1..=MAX_MINUTES).contains(&req.minutes) {
        bail!("duration must be between 1 and {MAX_MINUTES} minutes");
    }
    let (dir, warning) = records_dir(cfg)?;
    let now = chrono::Local::now();
    let output = dir.join(format!("{}_{}.ts", sanitize_filename(req.channel_name), now.format("%Y-%m-%d_%H%M")));
    let unit = format!("kdcli-now-{}", now.format("%Y%m%d-%H%M%S"));
    let seconds = req.minutes * 60;

    let mut command = vec![
        cfg.python().display().to_string(),
        cfg.cli_dir.join("scripts/record_scheduled.py").display().to_string(),
        "--stream-id".into(),
        req.stream_id.to_string(),
        "--duration".into(),
        seconds.to_string(),
        "--output".into(),
        output.display().to_string(),
        "--channel-name".into(),
        req.channel_name.to_string(),
    ];
    match (req.show_name, cfg.tv_library_root()) {
        (Some(show), Some(root)) => command.extend([
            "--postprocess-library-dir".into(),
            root.join(show).display().to_string(),
            "--postprocess-show-name".into(),
            show.to_string(),
        ]),
        (Some(_), None) => bail!("no Jellyfin TV folder configured (TVSHOW_LIBRARY_ROOT in .env)"),
        (None, _) => command.push("--notify-discord".into()),
    }

    systemd::run(systemd::Run {
        unit: &unit,
        description: &format!("KD CLI recording: {}, now, {} min", req.channel_name, req.minutes),
        working_dir: &cfg.cli_dir,
        max_seconds: Some(seconds as u64 + 300),
        command,
    })?;
    crate::log::event(
        cfg,
        "record.now",
        json!({ "unit": unit, "stream_id": req.stream_id, "channel": req.channel_name, "minutes": req.minutes,
                "show": req.show_name, "output": output, "warning": warning }),
    );
    Ok(Started { unit: format!("{unit}.service"), output: output.display().to_string(), warning })
}

/// Stop a running recording. The file recorded so far is kept.
pub fn stop(cfg: &Config, unit: &str) -> Result<()> {
    let unit = if unit.ends_with(".service") { unit.to_string() } else { format!("{unit}.service") };
    let is_recording = ["kdcli-rec-", "kdcli-now-", "iptv-noovo-record", "iptv-record-"].iter().any(|p| unit.starts_with(p));
    if !is_recording {
        bail!("{unit} is not a recording");
    }
    let out = systemd::systemctl(&["stop", &unit])?;
    if !out.status.success() {
        bail!("could not stop {unit}: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    // A one-time schedule removes itself after its run; a stopped run never gets there.
    if let Some(id) = unit.strip_prefix("kdcli-rec-").and_then(|u| u.strip_suffix(".service"))
        && crate::schedules::is_one_time(&cfg.data_dir, id)
    {
        crate::schedules::delete(cfg, id)?;
    }
    crate::log::event(cfg, "record.stop", json!({ "unit": unit }));
    Ok(())
}

/// The recordings folder if it accepts files, else `data/recordings` (an unplugged USB
/// drive is the usual reason), the same fallback as the Python recorder.
pub fn records_dir(cfg: &Config) -> Result<(PathBuf, Option<String>)> {
    let preferred = cfg.records_dir();
    let problem = match writable(&preferred) {
        None => return Ok((preferred, None)),
        Some(p) => p,
    };
    let fallback = cfg.data_dir.join("recordings");
    if let Some(fallback_problem) = writable(&fallback) {
        bail!("recordings folder unusable: {problem}; fallback {fallback_problem}");
    }
    let warning = format!("{problem}; recording to {} instead", fallback.display());
    Ok((fallback, Some(warning)))
}

fn writable(dir: &Path) -> Option<String> {
    if let Err(e) = std::fs::create_dir_all(dir) {
        return Some(format!("cannot create {}: {e}", dir.display()));
    }
    let probe = dir.join(".write_test");
    match std::fs::write(&probe, b"test").and_then(|_| std::fs::remove_file(&probe)) {
        Ok(()) => None,
        Err(e) => Some(format!("cannot write to {}: {e}", dir.display())),
    }
}

/// "CA FR: NOOVO V-TELE HD" → "ca_fr_noovo_v-tele_hd", like `recordings.sanitize_filename`.
pub fn sanitize_filename(name: &str) -> String {
    let kept: String = name.chars().filter(|c| c.is_alphanumeric() || matches!(c, ' ' | '-' | '_')).collect();
    kept.trim_end().replace(' ', "_").to_lowercase().chars().take(50).collect()
}

#[cfg(test)]
mod tests {
    use super::sanitize_filename;

    #[test]
    fn file_names_match_the_python_recorder() {
        assert_eq!(sanitize_filename("CA FR: NOOVO V-TELE HD"), "ca_fr_noovo_v-tele_hd");
        assert_eq!(
            sanitize_filename("US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET"),
            "us_espn_266__northeastern_vs_19_massachusetts_oct_"
        );
    }
}
