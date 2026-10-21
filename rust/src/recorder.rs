//! The recorder: capture a live channel with FFmpeg until the planned end time,
//! surviving a busy or dropping provider, then post-process and report.
//!
//! The provider refuses new connections (HTTP 458) while its one connection is in
//! use, and FFmpeg gives up on that in under a second. So the recorder retries
//! until the show would have ended; after footage has been captured, each retry
//! writes its own `<name>.partN.ts` so nothing already recorded is lost.
//!
//! Runs inside a systemd unit: `kdcli-rec-<id>` (a schedule's timer, through
//! `kdtv run-schedule`) or `kdcli-now-*` (record now). It logs to the journal and
//! to `data/logs/recording_<time>.log`, and tells the story in the run diary.

use crate::config::Config;
use crate::diary::{Attempt, Report, FAILED, OK, WARNING};
use crate::pipeline;
use crate::runs::format_seconds;
use anyhow::{Context, Result};
use serde_json::json;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const RETRY_DELAY_SECONDS: u64 = 30;
const MIN_USEFUL_SECONDS: i64 = 60;
/// FFmpeg prints progress twice a second; the log keeps one line a minute.
const PROGRESS_LOG_SECONDS: u64 = 60;

pub struct Job {
    pub stream_id: i64,
    pub seconds: i64,
    pub output: PathBuf,
    pub channel_name: String,
    /// Set: the episode goes into this Jellyfin show folder under this name.
    pub show: Option<(PathBuf, String)>,
    /// Ping Discord on success too (failures always ping).
    pub notify: bool,
    /// Why the recording is not on the usual drive, if it isn't.
    pub drive_warning: Option<String>,
}

/// Record, post-process, report. Returns the process exit code: 1 when the run failed.
pub fn run(cfg: &Config, job: &Job) -> i32 {
    let log = Log::open(cfg);
    log.info(&"=".repeat(60));
    log.info("KDTV recording started");
    log.info(&format!("Stream ID: {}  Channel: {}", job.stream_id, job.channel_name));
    log.info(&format!("Duration: {}s  Output: {}", job.seconds, job.output.display()));

    let run_id = job.output.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_else(|| "recording".into());
    let mut report = Report::new(
        cfg,
        &run_id,
        json!({
            "channel_name": job.channel_name,
            "stream_id": job.stream_id,
            "planned_seconds": job.seconds,
            "output": job.output,
            "show_name": job.show.as_ref().map(|(_, name)| name),
        }),
    );
    report.event("start", OK, &format!("recording started: {}, {} planned", job.channel_name, format_seconds(Some(job.seconds as f64))), json!({}));
    if let Some(w) = &job.drive_warning {
        report.event("drive", WARNING, &format!("the recordings drive is not usable ({w}); recorded to the local fallback folder instead"), json!({}));
    }

    if cfg.provider.server.is_empty() || cfg.provider.username.is_empty() || cfg.provider.password.is_empty() {
        log.error("Missing provider login (IPTV_SERVER_URL, IPTV_USERNAME, IPTV_PASSWORD in .env)");
        report.event("start", FAILED, "could not load the provider login (IPTV_* in .env)", json!({}));
        finish_and_notify(cfg, &mut report, job, &log);
        return 1;
    }

    let success = record_stream(cfg, job, &mut report, &log);
    log.info(&format!("Recording {}", if success { "COMPLETED" } else { "FAILED" }));

    let attempts = report.list("attempts").to_vec();
    let captured: i64 = attempts.iter().filter_map(|a| a["captured_seconds"].as_i64()).sum();
    let dropouts = attempts.iter().filter(|a| a["ok"] == false && a["wrote_data"] == true).count();
    let pieces = attempts.iter().filter(|a| a["wrote_data"] == true).count();
    report.set_result(json!({ "captured_seconds": captured, "dropouts": dropouts, "pieces": pieces }));
    let captured_text = format_seconds(Some(captured as f64));
    if success {
        report.event("recording", OK, &format!("captured {captured_text} in {} attempt(s), {dropouts} provider dropout(s)", attempts.len()), json!({}));
    } else {
        let last_error = attempts.iter().rev().find_map(|a| a["error"].as_str().map(str::to_string));
        let mut text = if captured > 0 {
            format!("recording failed: captured {captured_text} of {} planned", format_seconds(Some(job.seconds as f64)))
        } else {
            "recording failed: nothing was captured".into()
        };
        if let Some(e) = last_error {
            text += &format!(" (last error: {e}; {} attempts)", attempts.len());
        }
        report.event("recording", FAILED, &text, json!({}));
    }

    if success {
        match &job.show {
            Some((dir, name)) => {
                log.info("Post-processing: convert, join, place in Jellyfin");
                pipeline::postprocess(cfg, &job.output, dir, name, job.seconds, chrono::Local::now().date_naive(), &mut report);
            }
            None => {
                // No library step: still say how much footage the pieces hold.
                let actual: f64 = pipeline::find_segments(&job.output).iter().filter_map(|p| pipeline::media_seconds(p)).sum();
                report.set_result(json!({ "final_seconds": actual }));
                report.event("length", pipeline::length_status(actual, job.seconds), &pipeline::length_text(actual, job.seconds), json!({}));
            }
        }
    }

    finish_and_notify(cfg, &mut report, job, &log);
    log.info(&"=".repeat(60));
    if report.status() == FAILED { 1 } else { 0 }
}

/// Retry until the planned end time. True when one attempt ran to the end.
fn record_stream(cfg: &Config, job: &Job, report: &mut Report, log: &Log) -> bool {
    if let Some(dir) = job.output.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let url = cfg.provider.stream_url(job.stream_id);
    let deadline = Instant::now() + Duration::from_secs(job.seconds as u64);
    let remaining = || deadline.saturating_duration_since(Instant::now()).as_secs() as i64;
    let mut captured_any = false;
    let mut attempt = 0u32;
    loop {
        attempt += 1;
        let left = remaining();
        if left < MIN_USEFUL_SECONDS {
            log.error("Giving up: less than a minute of the requested window remains");
            return captured_any;
        }
        // Keep earlier footage: every retry after real capture gets its own file.
        let target = if captured_any { job.output.with_extension(format!("part{attempt}.ts")) } else { job.output.clone() };
        if attempt > 1 {
            log.info(&format!("Retry {}: {left}s of the window left, recording to {}", attempt - 1, target.display()));
        }

        let once = record_once(cfg, &url, &target, left, &job.channel_name, log);
        captured_any |= once.wrote;
        let size_mb = target.metadata().map(|m| m.len() as f64 / 1048576.0).unwrap_or(0.0);
        report.attempt(Attempt {
            number: attempt,
            file: &target,
            planned_seconds: left,
            captured_seconds: once.elapsed,
            size_mb,
            ok: once.ok,
            wrote: once.wrote,
            error: once.error.as_deref(),
        });
        // A real dropout always gets a line; repeated empty attempts only the first few.
        if !once.ok && (once.wrote || attempt <= 3) {
            let reason = once.error.as_deref().unwrap_or("no reason given");
            let text = if once.wrote {
                format!("provider dropout after {} of footage ({reason}); retrying", format_seconds(Some(once.elapsed)))
            } else {
                format!("attempt {attempt} got no data from the provider ({reason}); retrying")
            };
            report.event("recording", WARNING, &text, json!({}));
        }
        if once.ok {
            return true;
        }
        let left = remaining();
        if left < MIN_USEFUL_SECONDS {
            log.error("Recording window has run out; stopping retries");
            return captured_any;
        }
        log.warn(&format!("Attempt {attempt} failed; retrying in {RETRY_DELAY_SECONDS}s"));
        std::thread::sleep(Duration::from_secs(RETRY_DELAY_SECONDS.min((left - MIN_USEFUL_SECONDS).max(0) as u64)));
    }
}

struct Once {
    ok: bool,
    wrote: bool,
    /// Seconds of footage (or of wall clock, whichever is larger).
    elapsed: f64,
    error: Option<String>,
}

/// One FFmpeg capture of at most `seconds`.
fn record_once(cfg: &Config, url: &str, output: &Path, seconds: i64, channel_name: &str, log: &Log) -> Once {
    let mut once = Once { ok: false, wrote: false, elapsed: 0.0, error: None };
    let mut cmd = Command::new("ffmpeg");
    cmd.args(["-nostdin", "-y", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_at_eof", "1"])
        .args(["-reconnect_delay_max", "30", "-rw_timeout", "30000000", "-i", url, "-c", "copy"])
        .args(["-t", &seconds.to_string()])
        .args(["-metadata", &format!("title={channel_name}")])
        .args(["-metadata", &format!("date={}", chrono::Local::now().format("%Y-%m-%d"))])
        .arg(output)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());

    log.info("Starting recording (provider URL hidden: it contains the login)");
    log.info(&format!("  Output: {}  Duration: {seconds}s", output.display()));
    let started = Instant::now();
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            once.error = Some(if e.kind() == std::io::ErrorKind::NotFound { "ffmpeg not installed".into() } else { e.to_string() });
            log.error(once.error.as_deref().unwrap_or(""));
            return once;
        }
    };

    // A stalled stream can hang FFmpeg past its -t: stop it five minutes after.
    let done = Arc::new(AtomicBool::new(false));
    let pid = child.id() as i32;
    {
        let done = done.clone();
        let limit = Duration::from_secs(seconds as u64 + 300);
        std::thread::spawn(move || {
            let until = Instant::now() + limit;
            while Instant::now() < until {
                if done.load(Ordering::Relaxed) {
                    return;
                }
                std::thread::sleep(Duration::from_secs(5));
            }
            if !done.load(Ordering::Relaxed) {
                // SAFETY: plain kill(2) on our own child's pid.
                unsafe { libc::kill(pid, libc::SIGTERM) };
            }
        });
    }

    // FFmpeg ends progress lines with \r, everything else with \n.
    let mut last_progress: Option<Instant> = None;
    if let Some(stderr) = child.stderr.take() {
        let mut reader = BufReader::new(stderr);
        let mut buf = vec![];
        loop {
            buf.clear();
            match read_line(&mut reader, &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            let line = cfg.provider.redact(String::from_utf8_lossy(&buf).trim()).replace(url, "<provider-url>");
            if line.is_empty() {
                continue;
            }
            let lower = line.to_lowercase();
            const IMPORTANT: [&str; 11] = ["error", "warning", "time=", "size=", "eof", "connection", "reconnect", "invalid", "failed", "timed out", "http"];
            if !IMPORTANT.iter().any(|w| lower.contains(w)) {
                continue;
            }
            let decoder_noise = lower.contains("[h264") || lower.contains("[aac") || lower.contains("decoding");
            if !decoder_noise && (lower.contains("error") || lower.contains("failed") || lower.contains("timed out")) {
                once.error = Some(line.clone());
            }
            let progress = lower.contains("time=") && lower.contains("size=") && !lower.contains("error");
            if progress && !lower.contains("lsize=") {
                if last_progress.is_some_and(|t| t.elapsed() < Duration::from_secs(PROGRESS_LOG_SECONDS)) {
                    continue;
                }
                last_progress = Some(Instant::now());
            }
            log.info(&format!("FFmpeg: {line}"));
        }
    }
    let status = child.wait();
    done.store(true, Ordering::Relaxed);

    // A live stream sends a burst at the start, so footage can be longer than the
    // wall-clock time it took. Judge by whichever is larger.
    let size = output.metadata().map(|m| m.len()).unwrap_or(0);
    once.elapsed = started.elapsed().as_secs_f64().max(if size > 0 { pipeline::media_seconds(output).unwrap_or(0.0) } else { 0.0 });
    once.wrote = size > 0;
    match status {
        Ok(s) if s.success() => {
            if size == 0 {
                log.error("FFmpeg exited successfully but produced no recording");
                once.error.get_or_insert_with(|| "ffmpeg produced no recording".into());
            } else if once.elapsed < seconds as f64 * 0.9 {
                log.error(&format!("Recording ended early after {:.0} of {seconds} requested seconds", once.elapsed));
                once.error.get_or_insert_with(|| format!("ended early after {:.0}s", once.elapsed));
            } else {
                log.info(&format!("Recording completed: {:.2} MB", size as f64 / 1048576.0));
                once.ok = true;
            }
        }
        Ok(s) => {
            let why = s.code().map_or("ffmpeg was stopped by a signal".to_string(), |c| format!("ffmpeg exit code {c}"));
            log.error(&why);
            once.error.get_or_insert(why);
        }
        Err(e) => {
            once.error.get_or_insert_with(|| e.to_string());
        }
    }
    once
}

/// Read up to and including the next \n or \r.
fn read_line(reader: &mut impl BufRead, buf: &mut Vec<u8>) -> std::io::Result<usize> {
    let mut byte = [0u8; 1];
    loop {
        match reader.read(&mut byte)? {
            0 => return Ok(buf.len()),
            _ => {
                buf.push(byte[0]);
                if byte[0] == b'\n' || byte[0] == b'\r' {
                    return Ok(buf.len());
                }
            }
        }
    }
}

/// Close the diary and tell Discord. Successes ping when the run fills a library or
/// asked for a ping; warnings and failures always ping so a problem is never silent.
fn finish_and_notify(cfg: &Config, report: &mut Report, job: &Job, log: &Log) {
    let name = &job.channel_name;
    let run_id = report.run_id().to_string();
    let actual = report.result("final_seconds").as_f64().or_else(|| report.result("captured_seconds").as_f64());
    let planned = format_seconds(Some(job.seconds as f64));
    let dropouts = report.result("dropouts").as_u64().unwrap_or(0);
    let dropout_note = if dropouts > 0 { format!(", {dropouts} dropout(s) repaired") } else { String::new() };
    let episode = report.result("episode_path").as_str().map(PathBuf::from);

    let (message, send) = match report.status() {
        OK => {
            let message = match &episode {
                Some(ep) => format!(
                    "✅ Recorded and added to Jellyfin: **{}** ({name}, {} of {planned}{dropout_note})",
                    ep.file_stem().map(|s| s.to_string_lossy()).unwrap_or_default(),
                    format_seconds(actual)
                ),
                None => format!(
                    "✅ Recorded: **{name}** ({} of {planned}{dropout_note}) → {}",
                    format_seconds(actual),
                    job.output.file_name().map(|s| s.to_string_lossy()).unwrap_or_default()
                ),
            };
            (message, job.show.is_some() || job.notify)
        }
        WARNING => (format!("⚠️ **{name}** recorded with problems: {} (run {run_id})", report.problems().into_iter().take(3).collect::<Vec<_>>().join("; ")), true),
        _ => {
            let failed = report.list("events").iter().find(|e| e["status"] == FAILED).and_then(|e| e["message"].as_str()).unwrap_or("unknown problem").to_string();
            (format!("❌ **{name}** failed: {failed} (run {run_id})"), true)
        }
    };
    if send {
        match crate::notify::discord(cfg, &message) {
            Ok(()) => {
                log.info("Discord notification sent");
                report.set_result(json!({ "discord_sent": true }));
            }
            Err(why) => {
                log.error(&format!("Discord notification failed: {why}"));
                report.set_result(json!({ "discord_sent": false }));
                report.event("discord", WARNING, "the Discord message could not be delivered", json!({}));
            }
        }
    }
    report.finish();
}

/// The journal (stdout) plus `data/logs/recording_<time>.log`, in the format the
/// older Python recorder used, so old and new logs read the same.
struct Log {
    file: Option<std::sync::Mutex<std::fs::File>>,
}

impl Log {
    fn open(cfg: &Config) -> Log {
        let dir = cfg.data_dir.join("logs");
        let path = dir.join(format!("recording_{}.log", chrono::Local::now().format("%Y%m%d_%H%M%S")));
        let file = std::fs::create_dir_all(&dir).and_then(|_| std::fs::File::create(path)).ok().map(std::sync::Mutex::new);
        Log { file }
    }

    fn write(&self, level: &str, message: &str) {
        let line = format!("{} - {level} - {message}", chrono::Local::now().format("%Y-%m-%d %H:%M:%S,%3f"));
        println!("{line}");
        if let Some(f) = &self.file
            && let Ok(mut f) = f.lock()
        {
            let _ = writeln!(f, "{line}");
        }
    }

    fn info(&self, m: &str) {
        self.write("INFO", m);
    }
    fn warn(&self, m: &str) {
        self.write("WARNING", m);
    }
    fn error(&self, m: &str) {
        self.write("ERROR", m);
    }
}

/// `kdtv run-schedule <id>`: what a schedule's timer starts. A fresh date-stamped
/// file, then the recorder; a one-time schedule removes itself once it has run.
pub fn run_schedule(cfg: &Config, id: &str) -> Result<i32> {
    let s = crate::schedules::load(&cfg.data_dir, id).with_context(|| format!("no such schedule: {id}"))?;
    if !s.enabled {
        return Ok(0);
    }
    let (dir, drive_warning) = crate::record::records_dir(cfg)?;
    let output = crate::record::output_path(&dir, &s.channel_name, chrono::Local::now());
    let show = match (&s.show_name, cfg.tv_library_root()) {
        (Some(name), Some(root)) => Some((root.join(name), name.clone())),
        (Some(_), None) => anyhow::bail!("schedule {id} names a show but TVSHOW_LIBRARY_ROOT is not set in .env"),
        (None, _) => None,
    };
    let job = Job { stream_id: s.stream_id, seconds: s.duration_min * 60, output, channel_name: s.channel_name.clone(), show, notify: s.notify, drive_warning };
    let code = run(cfg, &job);
    if s.days.is_empty() {
        crate::schedules::delete(cfg, id)?;
    }
    Ok(code)
}

/// `kdtv retry-run <run_id>`: rebuild a library episode from the pieces a run left
/// on disk (JP proposes it, the owner confirms). Writes its own report
/// (`<run_id>-retry-HHMM`) and pings Discord when done.
pub fn retry(cfg: &Config, run_id: &str) -> Result<i32> {
    let original = crate::jp::eyes::load_run(cfg, run_id).with_context(|| format!("no report named {run_id}"))?;
    let text = |k: &str| original[k].as_str().map(str::to_string);
    let (Some(show), Some(output)) = (text("show_name"), text("output")) else {
        anyhow::bail!("{run_id} was not meant for the Jellyfin library");
    };
    let root = cfg.tv_library_root().context("TVSHOW_LIBRARY_ROOT is not set in .env")?;
    let planned = original["planned_seconds"].as_i64().unwrap_or(0);
    let started: String = text("started_at").unwrap_or_default().chars().take(10).collect();
    let air = chrono::NaiveDate::parse_from_str(&started, "%Y-%m-%d").context("the report has no valid start date")?;
    let mut report = Report::new(
        cfg,
        &format!("{run_id}-retry-{}", chrono::Local::now().format("%H%M")),
        json!({ "channel_name": original["channel_name"], "planned_seconds": planned, "output": output, "show_name": show, "retry_of": run_id }),
    );
    report.event("start", OK, &format!("rebuilding “{show}” of {air} from the pieces saved by {run_id}"), json!({}));
    let placed = pipeline::postprocess(cfg, Path::new(&output), &root.join(&show), &show, planned, air, &mut report);
    report.finish();

    let stem = |p: &PathBuf| p.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let message = match &placed {
        Some(p) if report.status() == OK => format!("✅ Rebuilt and added to Jellyfin: **{}**", stem(p)),
        Some(p) => format!("⚠️ Rebuilt **{}** with problems: {}", stem(p), report.problems().into_iter().take(3).collect::<Vec<_>>().join("; ")),
        None => {
            let failed = report.list("events").iter().find(|e| e["status"] == FAILED).and_then(|e| e["message"].as_str()).unwrap_or("unknown problem").to_string();
            format!("❌ Could not rebuild **{}**: {failed}", text("channel_name").unwrap_or(show))
        }
    };
    let _ = crate::notify::discord(cfg, &message);
    Ok(if placed.is_some() { 0 } else { 1 })
}
