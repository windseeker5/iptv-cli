//! Record now, and stop a recording.
//!
//! "Now" runs the same recorder the timers use (`recorder.rs`: FFmpeg with
//! retries, run diary, Jellyfin post-processing, Discord) as `kdtv record-worker`
//! in a transient systemd unit `kdcli-now-*`, so it outlives kdtv.

use crate::config::Config;
use crate::systemd;
use anyhow::{bail, Context, Result};
use serde::Serialize;
use serde_json::json;
use std::path::{Path, PathBuf};

/// Units that are recordings: scheduled, started now, the NOOVO jobs, and the old TUI's.
pub const UNIT_PATTERNS: &[&str] =
    &["kdcli-rec-*.service", "kdcli-now-*.service", "iptv-noovo-record*.service", "iptv-record-*.service"];

pub const MAX_MINUTES: i64 = 6 * 60;
/// The recorder gives up when less than a minute of the window is left at start.
pub const MIN_MINUTES: i64 = 2;

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
    pub minutes: i64,
    pub output: String,
    pub warning: Option<String>,
}

pub fn record_now(cfg: &Config, req: &Request) -> Result<Started> {
    if !(MIN_MINUTES..=MAX_MINUTES).contains(&req.minutes) {
        bail!("duration must be between {MIN_MINUTES} and {MAX_MINUTES} minutes");
    }
    let now = chrono::Local::now();
    // One provider connection: end a minute before the next scheduled recording starts.
    let mut minutes = req.minutes;
    let mut notes = vec![];
    if let Some((start, what)) = crate::schedules::next_start_within(&cfg.data_dir, now.naive_local(), minutes + 1) {
        let room = (start - now.naive_local()).num_minutes() - 1;
        if room < MIN_MINUTES {
            bail!("“{what}” starts at {}; there is no room for another recording before it", start.format("%H:%M"));
        }
        minutes = room;
        notes.push(format!("shortened to {minutes} min so it ends before “{}” at {}", what.split(',').next().unwrap_or(&what), start.format("%H:%M")));
    }
    let (dir, folder_warning) = records_dir(cfg)?;
    notes.extend(folder_warning.clone());
    let warning = (!notes.is_empty()).then(|| notes.join("; "));
    let output = output_path(&dir, req.channel_name, now);
    let unit = format!("kdcli-now-{}", now.format("%Y%m%d-%H%M%S"));
    let seconds = minutes * 60;

    let exe = std::env::current_exe().context("finding the kdtv program")?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.extend([
        "record-worker".into(),
        req.stream_id.to_string(),
        "--seconds".into(),
        seconds.to_string(),
        "--output".into(),
        output.display().to_string(),
        "--channel-name".into(),
        req.channel_name.to_string(),
    ]);
    match (req.show_name, cfg.tv_library_root()) {
        (Some(show), Some(_)) => command.extend(["--show".into(), show.to_string()]),
        (Some(_), None) => bail!("no Jellyfin TV folder configured (TVSHOW_LIBRARY_ROOT in .env)"),
        (None, _) => {}
    }
    if let Some(w) = folder_warning {
        command.extend(["--drive-warning".into(), w]);
    }

    systemd::run(systemd::Run {
        unit: &unit,
        description: &format!("KD CLI recording: {}, now, {minutes} min", req.channel_name),
        working_dir: &cfg.cli_dir,
        // The recording, plus time to convert and place a long show.
        max_seconds: Some(seconds as u64 + 1800),
        command,
    })?;
    crate::log::event(
        cfg,
        "record.now",
        json!({ "unit": unit, "stream_id": req.stream_id, "channel": req.channel_name, "minutes": minutes,
                "show": req.show_name, "output": output, "warning": warning }),
    );
    Ok(Started { unit: format!("{unit}.service"), minutes, output: output.display().to_string(), warning })
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
/// drive is the usual reason).
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

/// `<folder>/ca_fr_noovo_v-tele_hd_2026-10-21_1829.ts`
pub fn output_path(dir: &Path, channel_name: &str, at: chrono::DateTime<chrono::Local>) -> PathBuf {
    dir.join(format!("{}_{}.ts", sanitize_filename(channel_name), at.format("%Y-%m-%d_%H%M")))
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
