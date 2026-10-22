//! JP's eyes: read-only checks for the owner. Everything here only LOOKS: run
//! reports, media files, the provider, logs and code. Changes go through a
//! proposal and a Confirm tap (`actions.rs`).
//!
//! Rules every function follows:
//! - Secrets never leave this module: provider logins, API keys, tokens and the
//!   Discord webhook are redacted from anything returned (`redact`).
//! - Files and code are read only from an allowlist; .env files, databases and
//!   data folders are never readable.
//! - Short: the runner gives a chat turn 180 s, so probes sample seconds, never
//!   whole files. Nothing here prints to stdout (the MCP server speaks on it).

use crate::config::Config;
use crate::{pipeline, restream, runs, schedules, status};
use regex::Regex;
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

const SKIP_DIRS: &[&str] = &[
    "venv", ".git", "data", "downloads", "__pycache__", "node_modules", "hls", "nginx", "build", ".gradle", "static", "media",
    "recordings", "hero", "target",
];
const SOURCE_SUFFIXES: &[&str] =
    &["rs", "py", "sh", "md", "txt", "html", "js", "css", "service", "timer", "yml", "yaml", "toml", "conf"];
const MEDIA_SUFFIXES: &[&str] = &["ts", "mp4", "mkv"];
/// A "running" report older than this is a crashed run, not a live one.
const RUNNING_STALE_HOURS: i64 = 6;

fn dev_root(cfg: &Config) -> PathBuf {
    cfg.cli_dir.parent().and_then(Path::parent).map(Path::to_path_buf).unwrap_or_else(|| cfg.cli_dir.clone())
}

fn source_roots(cfg: &Config) -> Vec<PathBuf> {
    vec![cfg.cli_dir.clone(), dev_root(cfg).join("OMT")]
}

// ── secrets ──────────────────────────────────────────────────────────────

/// Values of secret-looking keys in both .env files and the environment, longest first.
fn secret_values(cfg: &Config) -> &'static [String] {
    static VALUES: OnceLock<Vec<String>> = OnceLock::new();
    VALUES.get_or_init(|| {
        let key = Regex::new(r"(?i)PASSWORD|API_KEY|TOKEN|SECRET|WEBHOOK|IPTV_USERNAME|IPTV_SERVER_URL").unwrap();
        let mut values: Vec<String> = vec![];
        for file in [cfg.env_file.clone(), dev_root(cfg).join(".env")] {
            for line in std::fs::read_to_string(file).unwrap_or_default().lines() {
                if let Some((k, v)) = line.split_once('=')
                    && !line.trim_start().starts_with('#')
                    && key.is_match(k)
                {
                    values.push(v.trim().trim_matches(['\'', '"']).to_string());
                }
            }
        }
        values.extend(std::env::vars().filter(|(k, _)| key.is_match(k)).map(|(_, v)| v));
        values.retain(|v| v.len() >= 5);
        values.sort_by_key(|v| std::cmp::Reverse(v.len()));
        values.dedup();
        values
    })
}

/// Strip anything that could be a credential from text JP will see.
pub fn redact(cfg: &Config, text: &str) -> String {
    static PATTERNS: OnceLock<[(Regex, &str); 3]> = OnceLock::new();
    let patterns = PATTERNS.get_or_init(|| {
        [
            (Regex::new(r"/live/[^/\s]+/[^/\s]+/").unwrap(), "/live/***/***/"),
            (Regex::new(r"(?i)(username|password|token|api_key)=[^&\s]+").unwrap(), "${1}=***"),
            (Regex::new(r"https?://(?:discord(?:app)?\.com)/api/webhooks/\S+").unwrap(), "<discord-webhook>"),
        ]
    });
    let mut out = text.to_string();
    for value in secret_values(cfg) {
        out = out.replace(value.as_str(), "***");
    }
    for (re, with) in patterns {
        out = re.replace_all(&out, *with).into_owned();
    }
    out
}

fn run(cmd: &mut Command) -> Option<std::process::Output> {
    cmd.stdin(Stdio::null()).output().ok()
}

fn stdout(cmd: &mut Command) -> String {
    run(cmd).map(|o| String::from_utf8_lossy(&o.stdout).to_string()).unwrap_or_default()
}

// ── recordings: the diary ────────────────────────────────────────────────

pub fn load_run(cfg: &Config, run_id: &str) -> Option<Value> {
    if run_id.is_empty() || !run_id.chars().all(|c| c.is_alphanumeric() || matches!(c, '_' | '.' | '-')) {
        return None;
    }
    serde_json::from_str(&std::fs::read_to_string(cfg.data_dir.join("runs").join(format!("{run_id}.json"))).ok()?).ok()
}

fn is_stale(started_at: Option<&str>) -> bool {
    started_at
        .and_then(|s| chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S").ok())
        .is_some_and(|t| (chrono::Local::now().naive_local() - t).num_hours() >= RUNNING_STALE_HOURS)
}

/// The latest recordings with their outcome. Start here for "how did it go?".
pub fn recent_runs(cfg: &Config, limit: usize) -> Value {
    let mut list = json!(runs::recent(&cfg.data_dir, limit.clamp(1, 20)));
    for r in list.as_array_mut().into_iter().flatten() {
        if r["status"] == "running" && is_stale(r["start_time"].as_str()) {
            r["status"] = "crashed".into();
            if let Some(p) = r["problems"].as_array_mut() {
                p.push("the run never finished (stopped without a report)".into());
            }
        }
    }
    json!({ "runs": list, "note": "status: ok / warning / failed / running / stopped / crashed. Use run_report for the steps." })
}

/// The step-by-step diary of one recording.
pub fn run_report(cfg: &Config, run_id: &str) -> Value {
    let Some(d) = load_run(cfg, run_id) else {
        return json!({ "error": format!("no report named {run_id}"), "hint": "use recent_runs to list them" });
    };
    let events: Vec<String> = d["events"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|e| {
            let s = |k: &str| e[k].as_str().unwrap_or("").to_string();
            format!("{} [{}] {}: {}", s("time"), s("status"), s("step"), redact(cfg, &s("message")))
        })
        .collect();
    json!({
        "run_id": d["run_id"], "status": d["status"], "channel": d["channel_name"], "show_name": d["show_name"],
        "started_at": d["started_at"], "finished_at": d["finished_at"],
        "planned": runs::format_seconds(d["planned_seconds"].as_f64()),
        "events": events, "attempts": d["attempts"], "result": d["result"],
        "pieces_still_on_disk": pieces_on_disk(d["output"].as_str()),
    })
}

fn pieces_on_disk(output: Option<&str>) -> Vec<Value> {
    let Some(output) = output.filter(|o| !o.is_empty()) else { return vec![] };
    pipeline::find_segments(Path::new(output))
        .iter()
        .map(|p| json!({ "name": file_name(p), "size_mb": size(p) / 1048576 }))
        .collect()
}

fn size(p: &Path) -> u64 {
    p.metadata().map(|m| m.len()).unwrap_or(0)
}

fn file_name(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
}

// ── files: does it look good? ────────────────────────────────────────────

fn fallback_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("recordings")
}

fn media_roots(cfg: &Config) -> Vec<PathBuf> {
    [Some(fallback_dir(cfg)), cfg.tv_library_root(), Some(cfg.records_dir())]
        .into_iter()
        .flatten()
        .filter_map(|r| r.canonicalize().ok())
        .collect()
}

/// A run id or a path, resolved to a media file inside the allowed folders.
fn media_path(cfg: &Config, target: &str) -> (Option<PathBuf>, Option<Value>) {
    let mut target = target.to_string();
    let report = load_run(cfg, &target);
    if let Some(d) = &report {
        let candidates = [d["result"]["episode_path"].as_str(), d["output"].as_str()];
        let found = match candidates.into_iter().flatten().find(|c| Path::new(c).exists()) {
            Some(c) => Some(c.to_string()),
            None => pipeline::find_segments(Path::new(d["output"].as_str().unwrap_or(""))).first().map(|p| p.display().to_string()),
        };
        match found {
            Some(f) => target = f,
            None => return (None, report),
        }
    }
    let mut p = PathBuf::from(&target);
    if !p.is_absolute() {
        p = fallback_dir(cfg).join(p);
    }
    let Ok(p) = p.canonicalize() else { return (None, report) };
    let media = p.extension().is_some_and(|e| MEDIA_SUFFIXES.contains(&e.to_string_lossy().to_lowercase().as_str()));
    if !media || !media_roots(cfg).iter().any(|root| p.starts_with(root)) {
        return (None, report);
    }
    (Some(p), report)
}

/// Length, streams and a picture/sound check, sampling a few short stretches for
/// black picture, frozen picture, silence and decode errors.
pub fn check_file(cfg: &Config, target: &str, samples: usize) -> Value {
    let (Some(path), report) = media_path(cfg, target) else {
        return json!({ "error": "file not found or not in an allowed folder",
                       "hint": "pass a run id from recent_runs, or a path inside the recordings / TV library folders" });
    };
    let modified = path.metadata().and_then(|m| m.modified()).ok().map(|t| chrono::DateTime::<chrono::Local>::from(t).format("%Y-%m-%dT%H:%M").to_string());
    let mut out = json!({ "file": file_name(&path), "folder": path.parent().map(|p| p.display().to_string()),
                          "size_mb": size(&path) / 1048576, "modified": modified });
    let probe = run(Command::new("ffprobe")
        .args(["-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,channels", "-of", "json"])
        .arg(&path));
    let info: Value = probe.as_ref().and_then(|o| serde_json::from_slice(&o.stdout).ok()).unwrap_or_default();
    let Some(duration) = info["format"]["duration"].as_str().and_then(|d| d.parse::<f64>().ok()) else {
        let detail = probe.map(|o| String::from_utf8_lossy(&o.stderr).to_string()).unwrap_or_default();
        out["error"] = "the file cannot be read as video (corrupt or incomplete)".into();
        out["detail"] = redact(cfg, &detail[detail.len().saturating_sub(300)..]).into();
        return out;
    };
    out["duration"] = runs::format_seconds(Some(duration)).into();
    out["duration_seconds"] = (duration.round() as i64).into();
    for s in info["streams"].as_array().into_iter().flatten() {
        let v = |k: &str| s[k].as_str().map(str::to_string).or_else(|| s[k].as_i64().map(|n| n.to_string())).unwrap_or_default();
        match s["codec_type"].as_str() {
            Some("video") => out["video"] = format!("{} {}x{} @ {}", v("codec_name"), v("width"), v("height"), v("r_frame_rate")).into(),
            Some("audio") => out["audio"] = format!("{} {}ch", v("codec_name"), v("channels")).into(),
            _ => {}
        }
    }
    let planned = report.as_ref().and_then(|r| r["planned_seconds"].as_f64()).filter(|p| *p > 0.0);
    let percent = planned.map(|p| (100.0 * duration / p).round() as i64);
    if let (Some(p), Some(pct)) = (planned, percent) {
        out["planned"] = runs::format_seconds(Some(p)).into();
        out["percent_of_planned"] = pct.into();
    }

    // Decoder complaints only (timestamp chatter from the muxer is harmless).
    let bad = Regex::new(r"(?i)error|invalid|exceeds|not allocated|reserved|corrupt|not allowed|failure|does not match").unwrap();
    let audio_line = Regex::new(r"^\[(aac|ac3|eac3|mp2|mp3)").unwrap();
    let n = samples.clamp(2, 8);
    let mut findings = vec![];
    let mut flagged = 0;
    for i in 0..n {
        let at = duration * (0.05 + 0.9 * i as f64 / (n - 1) as f64);
        let text = run(Command::new("ffmpeg")
            .args(["-nostdin", "-hide_banner", "-v", "info", "-ss", &format!("{at:.1}"), "-t", "8", "-i"])
            .arg(&path)
            .args(["-vf", "blackdetect=d=2:pix_th=0.10,freezedetect=n=0.003:d=4", "-af", "silencedetect=n=-50dB:d=4", "-f", "null", "-"]))
        .map(|o| String::from_utf8_lossy(&o.stderr).to_string())
        .unwrap_or_default();
        let video_errors = text.lines().filter(|l| l.starts_with("[h264") && bad.is_match(l)).count();
        let audio_errors = text.lines().filter(|l| audio_line.is_match(l) && bad.is_match(l)).count();
        let (black, frozen, silence) = (text.contains("black_start"), text.contains("freeze_start"), text.contains("silence_start"));
        if black || frozen || silence || video_errors > 5 || audio_errors > 5 {
            flagged += 1;
        }
        findings.push(json!({ "at": runs::format_seconds(Some(at)), "black_picture": black, "frozen_picture": frozen,
                              "silence": silence, "picture_errors": video_errors, "sound_errors": audio_errors }));
    }
    let damaged = |key: &str| -> Vec<Value> { findings.iter().filter(|f| f[key].as_u64().unwrap_or(0) > 5).map(|f| f["at"].clone()).collect() };
    let (sound, picture) = (damaged("sound_errors"), damaged("picture_errors"));
    out["samples"] = json!(findings);
    if !sound.is_empty() {
        out["damaged_sound_at"] = json!(sound);
    }
    if !picture.is_empty() {
        out["damaged_picture_at"] = json!(picture);
    }
    out["verdict"] = match percent {
        Some(p) if p < 70 => "BAD: much shorter than planned".into(),
        Some(p) if p < 95 => "CHECK: shorter than planned".into(),
        _ if !sound.is_empty() || !picture.is_empty() => {
            let what = [("sound", !sound.is_empty()), ("picture", !picture.is_empty())].iter().filter(|x| x.1).map(|x| x.0).collect::<Vec<_>>().join(" and ");
            format!("CHECK: damaged {what} in some samples (see damaged_*_at); the damage usually runs from there to the next clean spot and is in the provider's feed, not fixable")
        }
        _ if flagged >= 2.max(n / 2) => "CHECK: many samples show black, frozen or silent stretches".into(),
        _ if flagged > 0 => "OK, with a few spots to look at (black/frozen can just be a title card or ad break)".into(),
        _ => "OK: full length, picture and sound present in every sample".into(),
    }
    .into();
    out
}

// ── provider and stream ──────────────────────────────────────────────────

/// Provider account info, without secrets.
pub fn provider_account(cfg: &Config) -> Value {
    let info = match crate::refresh::api(cfg, None) {
        Ok(v) => v,
        Err(e) => {
            let text = format!("{e:#}");
            // curl says "returned error: 403" when it got an answer, else it never connected.
            return match Regex::new(r"error: (\d{3})").unwrap().captures(&text) {
                Some(c) => json!({ "reachable": true, "http": c[1].parse::<i64>().unwrap_or(0), "login_ok": false }),
                None => json!({ "reachable": false, "error": redact(cfg, &text) }),
            };
        }
    };
    let u = &info["user_info"];
    let num = |v: &Value| v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok()));
    let days = num(&u["exp_date"]).map(|exp| ((exp - chrono::Utc::now().timestamp()) as f64 / 86400.0).round() as i64);
    json!({ "reachable": true, "http": 200, "login_ok": u["auth"] == 1 || u["auth"] == "1" || u["auth"] == true,
            "account_status": u["status"], "expires_in_days": days,
            "connections_in_use": num(&u["active_cons"]).unwrap_or(0),
            "connections_allowed": num(&u["max_connections"]).unwrap_or(0) })
}

/// Is the IPTV provider up, is the login accepted, is the one connection free?
pub fn probe_provider(cfg: &Config) -> Value {
    let mut out = provider_account(cfg);
    out["channel_list_age_minutes"] = json!(status::list_age_minutes(&cfg.data_dir).0);
    if out["connections_allowed"] == 1 {
        out["note"] = "The plan allows ONE connection. Watching TV, a restream, a stream test or a recording each use it; a second one gets refused or cuts the first.".into();
    }
    out
}

/// Why a stream test must not run now, or None when the provider's slot is free.
fn connection_busy(cfg: &Config) -> Option<String> {
    if let Some(r) = runs::recent(&cfg.data_dir, 5).into_iter().find(|r| r.status == "running" && !is_stale(r.start_time.as_deref())) {
        return Some(format!("a recording is running now ({})", r.channel_name.unwrap_or_default()));
    }
    if restream::active(&cfg.data_dir).is_some() {
        return Some("a restream is live and uses the provider's connection".into());
    }
    let acct = provider_account(cfg);
    let (used, allowed) = (acct["connections_in_use"].as_i64().unwrap_or(0), acct["connections_allowed"].as_i64().unwrap_or(0));
    (allowed > 0 && used >= allowed).then(|| "the provider says its connection is in use right now (someone is watching or recording)".into())
}

/// Pull a few seconds of a live channel and report whether the feed is healthy.
pub fn probe_stream(cfg: &Config, stream_id: i64, seconds: i64) -> Value {
    let seconds = seconds.clamp(4, 15);
    if let Some(busy) = connection_busy(cfg) {
        return json!({ "ok": false, "skipped": true, "reason": format!("not tested: {busy}") });
    }
    let url = cfg.provider.stream_url(stream_id);
    // A temp file (deleted right after): the null muxer reports no size or bitrate.
    let dir = std::env::temp_dir().join(format!("jp-probe-{}", crate::jp::random_hex(4)));
    let _ = std::fs::create_dir_all(&dir);
    let file = dir.join("probe.ts");
    let output = Command::new("timeout")
        .arg((seconds + 25).to_string())
        .args(["ffmpeg", "-nostdin", "-hide_banner", "-t", &seconds.to_string(), "-rw_timeout", "10000000", "-i", &url, "-map", "0", "-c", "copy", "-f", "mpegts"])
        .arg(&file)
        .stdin(Stdio::null())
        .output();
    let got = (size(&file) > 0).then(|| pipeline::media_seconds(&file)).flatten();
    let bytes = size(&file);
    let _ = std::fs::remove_dir_all(&dir);
    let Ok(output) = output else {
        return json!({ "ok": false, "reason": "ffmpeg could not be started" });
    };
    if output.status.code() == Some(124) {
        return json!({ "ok": false, "reason": "the provider did not answer in time (stalled stream)" });
    }
    let text = redact(cfg, &String::from_utf8_lossy(&output.stderr).replace(&url, "<stream>"));
    let error_line = Regex::new(r"HTTP error|Server returned|Connection|timed out|Input/output error|Invalid data").unwrap();
    let errors: Vec<&str> = text.lines().filter(|l| error_line.is_match(l)).map(str::trim).take(4).collect();
    let video = Regex::new(r"Video: (\w+).*?(\d{3,4}x\d{3,4}).*?([\d.]+) fps").unwrap().captures(&text).map(|c| format!("{} {} @ {} fps", &c[1], &c[2], &c[3]));
    let audio = Regex::new(r"Audio: (\w+)").unwrap().captures(&text).map(|c| c[1].to_string());
    let ok = output.status.success() && got.is_some_and(|g| g >= seconds as f64 * 0.7);
    json!({ "ok": ok, "asked_seconds": seconds, "received_seconds": got.map(|g| (g * 10.0).round() / 10.0),
            "bitrate_kbps": got.filter(|g| *g > 0.0).map(|g| (bytes as f64 * 8.0 / 1000.0 / g).round() as i64),
            "video": video, "audio": audio, "errors": errors })
}

// ── whole-system health ──────────────────────────────────────────────────

pub fn jellyfin_status(cfg: &Config) -> Value {
    let Some(base) = cfg.env("JELLYFIN_PUBLISHED_SERVER_URL") else {
        return json!({ "configured": false });
    };
    let base = base.trim_end_matches('/');
    let public = stdout(Command::new("curl").args(["-sS", "--fail", "--max-time", "10"]).arg(format!("{base}/System/Info/Public")));
    let Ok(info) = serde_json::from_str::<Value>(&public) else {
        return json!({ "configured": true, "reachable": false });
    };
    let mut out = json!({ "configured": true, "reachable": true, "version": info["Version"] });
    match cfg.env("JELLYFIN_API_KEY") {
        Some(key) => {
            let mut child = Command::new("curl")
                .args(["-sS", "--fail", "--max-time", "10", "-o", "/dev/null", "-K", "-"])
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
            let accepted = child.as_mut().ok().and_then(|c| {
                use std::io::Write;
                let mut stdin = c.stdin.take()?;
                let header = crate::notify::auth_header(&key).replace('"', "\\\"");
                writeln!(stdin, "url = \"{base}/System/Info\"\nheader = \"{header}\"").ok()?;
                drop(stdin);
                c.wait().ok()
            });
            out["api_key_accepted"] = accepted.is_some_and(|s| s.success()).into();
        }
        None => {
            out["api_key_accepted"] = Value::Null;
            out["note"] = "no API key configured; scans use the Jellyfin login instead".into();
        }
    }
    out
}

pub fn disk(path: &Path) -> Value {
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_encoded_bytes()) else {
        return json!({ "path": path, "error": "bad path" });
    };
    // SAFETY: statvfs fills the zeroed struct we pass; the path is a valid C string.
    let mut s: libc::statvfs = unsafe { std::mem::zeroed() };
    if unsafe { libc::statvfs(c.as_ptr(), &mut s) } != 0 {
        return json!({ "path": path, "error": "not mounted or not readable" });
    }
    let (block, total, free, avail) = (s.f_frsize as f64, s.f_blocks as f64, s.f_bfree as f64, s.f_bavail as f64);
    let used = total - free;
    json!({ "path": path, "free_gb": (avail * block / 1e8).round() / 10.0,
            "used_percent": if total > 0.0 { (100.0 * used / total).round() } else { 0.0 } })
}

pub fn records_drive(cfg: &Config) -> Value {
    let p = cfg.records_dir();
    let writable = p.is_dir() && probe_write(&p);
    json!({ "path": p, "exists": p.is_dir(), "writable": writable,
            "effect": if writable { Value::Null } else { "recordings go to the local fallback folder instead".into() } })
}

fn probe_write(dir: &Path) -> bool {
    let probe = dir.join(".jp_write_test");
    std::fs::write(&probe, b"x").and_then(|_| std::fs::remove_file(&probe)).is_ok()
}

fn service_result(unit: &str) -> Value {
    let text = stdout(Command::new("systemctl").args(["--user", "show", unit, "--property=Result,ExecMainStatus,InactiveEnterTimestamp", "--value"]));
    let lines: Vec<&str> = text.lines().collect();
    json!({ "result": lines.first(), "last_finished": lines.get(2) })
}

/// Recording pieces no live run owns: each is a recording that never reached the library.
pub fn leftover_pieces(cfg: &Config, limit: usize) -> Vec<Value> {
    let live: Vec<String> = runs::recent(&cfg.data_dir, 10).into_iter().filter(|r| r.status == "running").map(|r| r.id).collect();
    let part = Regex::new(r"\.part\d+$").unwrap();
    let mut files: Vec<(std::time::SystemTime, PathBuf)> = [fallback_dir(cfg), cfg.records_dir()]
        .iter()
        .filter_map(|d| std::fs::read_dir(d).ok())
        .flatten()
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.is_file() && p.extension().is_some_and(|x| x == "ts"))
        .filter_map(|p| Some((p.metadata().ok()?.modified().ok()?, p)))
        .collect();
    files.sort_by_key(|(t, _)| std::cmp::Reverse(*t));
    files
        .into_iter()
        .filter_map(|(modified, p)| {
            let stem = p.file_stem()?.to_string_lossy().to_string();
            let base = part.replace(&stem, "").to_string();
            (!live.contains(&base)).then(|| {
                let age = modified.elapsed().map(|d| d.as_secs() / 3600).unwrap_or(0);
                json!({ "name": file_name(&p), "size_mb": size(&p) / 1048576, "age_hours": age, "has_report": load_run(cfg, &base).is_some() })
            })
        })
        .take(limit)
        .collect()
}

pub fn last_preflight(cfg: &Config) -> Value {
    let text = std::fs::read_to_string(cfg.data_dir.join("preflight.jsonl")).unwrap_or_default();
    text.lines().last().and_then(|l| serde_json::from_str(l).ok()).unwrap_or(Value::Null)
}

/// One look at everything a recording depends on.
pub fn system_health(cfg: &Config) -> Value {
    let schedules: Vec<Value> = schedules::list(&cfg.data_dir)
        .into_iter()
        .map(|s| {
            let mut v = json!({ "id": s.id, "what": s.what, "enabled": s.enabled, "next_run": s.next_run });
            if let (Some(v), Value::Object(r)) = (v.as_object_mut(), service_result(&format!("kdcli-rec-{}.service", s.id))) {
                v.extend(r);
            }
            v
        })
        .collect();
    json!({
        "checked_at": chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string(),
        "provider": provider_account(cfg),
        "jellyfin": jellyfin_status(cfg),
        "recordings_drive": records_drive(cfg),
        "disk_recordings": disk(&cfg.records_dir()),
        "disk_library": cfg.tv_library_root().map(|p| disk(&p)),
        "restream": crate::jp::actions::restream_status(cfg),
        "schedules": schedules,
        "discord_webhook_configured": cfg.env("DISCORD_WEBHOOK_URL").is_some(),
        "leftover_pieces": leftover_pieces(cfg, 12),
        "last_preflight": last_preflight(cfg),
    })
}

// ── logs ─────────────────────────────────────────────────────────────────

/// Journal of a KDTV unit (recordings, refresh jobs, JP's runner). Progress noise
/// is dropped, credentials are redacted, output is capped. Without `unit`, lists them.
pub fn read_log(cfg: &Config, unit: Option<&str>, since: &str, grep: Option<&str>, lines: usize) -> Value {
    let Some(unit) = unit.filter(|u| !u.is_empty()) else {
        let text = stdout(Command::new("systemctl").args(["--user", "list-units", "--all", "--no-legend", "--plain", "kdcli-*"]));
        let mut units: Vec<&str> = text.lines().filter_map(|l| l.split_whitespace().next()).collect();
        units.sort();
        units.dedup();
        return json!({ "units": units, "hint": "pass one of these as `unit`; recordings are kdcli-rec-*.service" });
    };
    if !Regex::new(r"^kdcli-[\w.\-]+\.(service|timer)$").unwrap().is_match(unit) {
        return json!({ "error": "only kdcli-* units can be read" });
    }
    if !Regex::new(r"^[0-9A-Za-z :.\-]{1,40}$").unwrap().is_match(since) {
        return json!({ "error": "bad `since`; use e.g. '2026-10-11 18:00' or '6 hours ago'" });
    }
    let lines = lines.clamp(10, 200);
    let text = stdout(Command::new("journalctl").args(["--user", "-u", unit, &format!("--since={since}"), "--no-pager", "-o", "short-iso", "-n", "150000"]));
    let mut rows: Vec<&str> = text
        .lines()
        .filter(|l| !l.trim().is_empty() && !l.contains("FFmpeg: frame=") && !l.contains("non-existing") && !l.contains("no frame!"))
        .collect();
    if let Some(g) = grep.map(str::to_lowercase) {
        rows.retain(|l| l.to_lowercase().contains(&g));
    }
    let total = rows.len();
    let shown: Vec<String> = rows[total.saturating_sub(lines)..].iter().map(|l| redact(cfg, l).chars().take(300).collect()).collect();
    json!({ "unit": unit, "since": since, "matching_lines": total, "lines": shown,
            "note": (total > lines).then(|| format!("showing the last {lines} of {total}; narrow with grep or since")) })
}

// ── code ─────────────────────────────────────────────────────────────────

fn readable_source(cfg: &Config, p: &Path) -> bool {
    let name = file_name(p);
    let suffix_ok = p.extension().is_some_and(|e| SOURCE_SUFFIXES.contains(&e.to_string_lossy().to_lowercase().as_str()));
    if name.starts_with(".env") || !suffix_ok {
        return false;
    }
    let Ok(rp) = p.canonicalize() else { return false };
    source_roots(cfg).iter().filter_map(|r| r.canonicalize().ok()).any(|root| {
        rp.strip_prefix(&root).is_ok_and(|rel| {
            let parts: Vec<_> = rel.components().collect();
            !parts[..parts.len().saturating_sub(1)].iter().any(|c| SKIP_DIRS.contains(&c.as_os_str().to_string_lossy().as_ref()))
        })
    })
}

fn source_files(cfg: &Config) -> Vec<PathBuf> {
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
        entries.sort_by_key(|e| e.file_name());
        for e in entries {
            let name = e.file_name().to_string_lossy().to_string();
            let path = e.path();
            if path.is_dir() {
                if !name.starts_with('.') && !SKIP_DIRS.contains(&name.as_str()) {
                    walk(&path, out);
                }
            } else {
                out.push(path);
            }
        }
    }
    let mut out = vec![];
    for root in source_roots(cfg) {
        walk(&root, &mut out);
    }
    out.retain(|p| readable_source(cfg, p));
    out
}

/// Find where something is in the code (kdtv/cli and the OMT site).
pub fn search_source(cfg: &Config, query: &str, limit: usize) -> Value {
    let q = query.trim().to_lowercase();
    if q.chars().count() < 3 {
        return json!({ "error": "query must be at least 3 characters" });
    }
    let limit = limit.clamp(1, 50);
    let mut hits = vec![];
    for p in source_files(cfg) {
        let text = String::from_utf8_lossy(&std::fs::read(&p).unwrap_or_default()).to_string();
        for (n, line) in text.lines().enumerate() {
            if line.to_lowercase().contains(&q) {
                hits.push(json!({ "file": p, "line": n + 1, "text": redact(cfg, line.trim()).chars().take(200).collect::<String>() }));
                if hits.len() >= limit {
                    return json!({ "matches": hits, "truncated": true });
                }
            }
        }
    }
    json!({ "matches": hits, "truncated": false })
}

/// Read part of a code file (read-only, secrets redacted, never .env or data/).
pub fn read_source(cfg: &Config, path: &str, start: usize, lines: usize) -> Value {
    let mut p = PathBuf::from(path);
    if !p.is_absolute() {
        p = cfg.cli_dir.join(p);
    }
    if !p.is_file() || !readable_source(cfg, &p) {
        return json!({ "error": "that file is not readable here (only code and docs in kdtv/cli and OMT)" });
    }
    let text = String::from_utf8_lossy(&std::fs::read(&p).unwrap_or_default()).to_string();
    let rows: Vec<&str> = text.lines().collect();
    let start = start.max(1);
    let lines = lines.clamp(10, 200);
    let chunk: Vec<String> = rows.iter().enumerate().skip(start - 1).take(lines).map(|(i, l)| format!("{}: {}", i + 1, redact(cfg, l))).collect();
    json!({ "file": p.canonicalize().unwrap_or(p), "start": start, "end": start - 1 + chunk.len(), "total_lines": rows.len(), "text": chunk.join("\n") })
}
