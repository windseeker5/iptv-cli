//! After a recording: convert each .ts piece to MP4, join the pieces, check the
//! length against the plan, place the episode in a Jellyfin show folder with
//! season/episode naming, and ask Jellyfin to rescan.
//!
//! Each step reports its own failure to the diary instead of failing the run: by
//! the time this runs the recording itself has succeeded. Originals are kept until
//! the episode is safely in the library, and a failure never ships a partial file
//! as if it were the whole show.

use crate::diary::{Report, FAILED, OK, WARNING};
use crate::runs::format_seconds;
use chrono::{Datelike, NaiveDate};
use serde_json::json;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Share of the planned length that counts as a complete recording, and below
/// which it counts as failed.
pub const LENGTH_OK: f64 = 0.95;
pub const LENGTH_FAILED: f64 = 0.70;

/// OK, WARNING or FAILED for `actual` seconds of `planned`.
pub fn length_status(actual: f64, planned: i64) -> &'static str {
    let ratio = if planned > 0 { actual / planned as f64 } else { 1.0 };
    if ratio >= LENGTH_OK {
        OK
    } else if ratio >= LENGTH_FAILED {
        WARNING
    } else {
        FAILED
    }
}

/// "34m59s recorded of 35m00s planned (100%)"
pub fn length_text(actual: f64, planned: i64) -> String {
    let ratio = if planned > 0 { actual / planned as f64 } else { 1.0 };
    format!("{} recorded of {} planned ({:.0}%)", format_seconds(Some(actual)), format_seconds(Some(planned as f64)), ratio * 100.0)
}

/// Real duration of a media file via ffprobe, or None when unreadable.
pub fn media_seconds(path: &Path) -> Option<f64> {
    let out = Command::new("ffprobe")
        .args(["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"])
        .arg(path)
        .stdin(Stdio::null())
        .output()
        .ok()?;
    String::from_utf8_lossy(&out.stdout).lines().last()?.trim().parse().ok()
}

fn first_audio_codec(path: &Path) -> Option<String> {
    let out = Command::new("ffprobe")
        .args(["-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0"])
        .arg(path)
        .stdin(Stdio::null())
        .output()
        .ok()?;
    String::from_utf8_lossy(&out.stdout).lines().next().map(|l| l.trim().to_string()).filter(|c| !c.is_empty())
}

/// The recording's pieces in attempt order: `<name>.ts`, then `<name>.part2.ts`,
/// `<name>.part3.ts`… (one per retry after a provider dropout). A retry that never
/// got data leaves an empty file; it is skipped.
pub fn find_segments(primary: &Path) -> Vec<PathBuf> {
    let stem = primary.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let prefix = format!("{stem}.part");
    let mut parts: Vec<(u32, PathBuf)> = primary
        .parent()
        .and_then(|d| std::fs::read_dir(d).ok())
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().to_string();
            let n = name.strip_prefix(&prefix)?.strip_suffix(".ts")?.parse().ok()?;
            Some((n, e.path()))
        })
        .collect();
    parts.sort_by_key(|(n, _)| *n);
    std::iter::once(primary.to_path_buf())
        .chain(parts.into_iter().map(|(_, p)| p))
        .filter(|p| p.metadata().is_ok_and(|m| m.len() > 0))
        .collect()
}

/// Remux a .ts into an MP4 any TV can direct-play, keeping the source.
///
/// Video is always stream copy: a re-encode once forced a higher H.264 level and
/// frame rate than the source and made TVs judder. AAC audio is copied too, since
/// re-encoding it makes FFmpeg abort at the first damaged packet a dropout leaves;
/// other codecs (NOOVO switches to E-AC3 mid-stream, which Jellyfin's web client
/// can't play) become AAC. Done means as long as the source, whatever the exit code.
pub fn remux_for_tv(src: &Path) -> Result<PathBuf, String> {
    if !src.exists() {
        return Err("source file not found".into());
    }
    if src.extension().is_some_and(|e| e.eq_ignore_ascii_case("mp4")) {
        return Ok(src.to_path_buf());
    }
    let temp = src.with_extension("remux.mp4");
    let source_seconds = media_seconds(src);
    let copy_audio = first_audio_codec(src).as_deref() == Some("aac");

    // Preferred audio first, then subtitles off, then a full audio re-encode as
    // the last resort for a file whose copied audio will not mux.
    let mut tries = vec![(true, copy_audio), (false, copy_audio)];
    if copy_audio {
        tries.push((false, false));
    }
    let mut last_error = String::new();
    for (subs, copy) in tries {
        let _ = std::fs::remove_file(&temp);
        let mut cmd = Command::new("ffmpeg");
        cmd.args(["-nostdin", "-y", "-i"]).arg(src).args(["-map", "0:v", "-map", "0:a"]);
        if subs {
            cmd.args(["-map", "0:s?"]);
        }
        cmd.args(["-c:v", "copy"]);
        // The explicit AAC filter drops a damaged packet and carries on; without it
        // the muxer aborts the whole file at the first one.
        if copy {
            cmd.args(["-c:a", "copy", "-bsf:a", "aac_adtstoasc"]);
        } else {
            cmd.args(["-c:a", "aac", "-b:a", "192k"]);
        }
        if subs {
            cmd.args(["-c:s", "mov_text"]);
        }
        cmd.args(["-movflags", "+faststart"]).arg(&temp);
        let out = match cmd.stdin(Stdio::null()).output() {
            Ok(out) => out,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err("ffmpeg not installed".into()),
            Err(e) => return Err(e.to_string()),
        };
        last_error = String::from_utf8_lossy(&out.stderr).trim().lines().last().unwrap_or("").to_string();
        if complete(&temp, source_seconds, out.status.success()) {
            let final_path = src.with_extension("mp4");
            return std::fs::rename(&temp, &final_path)
                .map(|_| final_path)
                .map_err(|e| format!("could not move the converted file into place: {e}"));
        }
    }
    let _ = std::fs::remove_file(&temp);
    Err(format!("remux failed: {}", if last_error.is_empty() { "no complete output" } else { &last_error }))
}

/// Remux every non-MP4 video under the downloads folder, replacing each original
/// once its MP4 is complete. Reports each file to `each`; returns (done, failed).
pub fn remux_downloads(cfg: &crate::config::Config, mut each: impl FnMut(&str, Result<(), String>)) -> (usize, usize) {
    const VIDEO: &[&str] = &["mkv", "ts", "avi", "mov", "webm", "flv"];
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        for e in std::fs::read_dir(dir).into_iter().flatten().filter_map(Result::ok) {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out);
            } else {
                out.push(p);
            }
        }
    }
    let mut files = vec![];
    walk(&cfg.downloads_dir(), &mut files);
    files.retain(|p| p.extension().is_some_and(|e| VIDEO.contains(&e.to_string_lossy().to_lowercase().as_str())));
    files.sort();
    let (mut done, mut failed) = (0, 0);
    for f in files {
        let result = remux_for_tv(&f).and_then(|_| std::fs::remove_file(&f).map_err(|e| format!("converted, but the original stays: {e}")));
        if result.is_ok() { done += 1 } else { failed += 1 }
        each(&name(&f), result);
    }
    (done, failed)
}

fn complete(output: &Path, source_seconds: Option<f64>, exit_ok: bool) -> bool {
    if !output.metadata().is_ok_and(|m| m.len() > 0) {
        return false;
    }
    match source_seconds {
        None => exit_ok,
        Some(source) => media_seconds(output).is_some_and(|produced| produced >= source * 0.98),
    }
}

/// Stream-copy join of MP4 parts.
fn join_mp4(parts: &[PathBuf], output: &Path) -> Result<(), String> {
    let list = output.with_extension("concat_list.txt");
    let quoted = |p: &PathBuf| std::path::absolute(p).unwrap_or(p.clone()).display().to_string().replace('\'', "'\\''");
    let text: String = parts.iter().map(|p| format!("file '{}'\n", quoted(p))).collect();
    std::fs::write(&list, text).map_err(|e| e.to_string())?;
    let out = Command::new("ffmpeg")
        .args(["-nostdin", "-y", "-f", "concat", "-safe", "0", "-i"])
        .arg(&list)
        .args(["-map", "0", "-c", "copy", "-movflags", "+faststart"])
        .arg(output)
        .stdin(Stdio::null())
        .output();
    let _ = std::fs::remove_file(&list);
    let out = out.map_err(|e| e.to_string())?;
    if out.status.success() && output.metadata().is_ok_and(|m| m.len() > 0) {
        return Ok(());
    }
    let _ = std::fs::remove_file(output);
    let stderr = String::from_utf8_lossy(&out.stderr);
    Err(stderr.trim().lines().last().map(str::to_string).unwrap_or_else(|| format!("ffmpeg exit {:?}", out.status.code())))
}

/// `<show>/Season 2026/<show> - S2026E042 - 2026-10-21.mp4`: the season is the
/// year and the episode is one more than the files already in that season.
pub fn next_episode_path(show_dir: &Path, show_name: &str, air_date: NaiveDate) -> PathBuf {
    let season = show_dir.join(format!("Season {}", air_date.year()));
    let existing = std::fs::read_dir(&season)
        .map(|d| d.filter_map(Result::ok).filter(|e| e.path().extension().is_some_and(|x| x == "mp4")).count())
        .unwrap_or(0);
    season.join(format!("{show_name} - S{}E{:03} - {}.mp4", air_date.year(), existing + 1, air_date.format("%Y-%m-%d")))
}

/// Rename, or copy then delete when the library is on another drive.
fn move_file(from: &Path, to: &Path) -> std::io::Result<()> {
    if std::fs::rename(from, to).is_ok() {
        return Ok(());
    }
    std::fs::copy(from, to)?;
    std::fs::remove_file(from)
}

/// Convert, join, check length, place into the library, rescan Jellyfin.
/// Returns the episode's path once it is in the library.
pub fn postprocess(cfg: &crate::config::Config, ts: &Path, show_dir: &Path, show_name: &str, planned_seconds: i64, air_date: NaiveDate, report: &mut Report) -> Option<PathBuf> {
    let segments = find_segments(ts);
    if segments.is_empty() {
        step(report, "convert", FAILED, &format!("recording file not found: {}", name(ts)));
        return None;
    }
    let folder = ts.parent().unwrap_or(Path::new(".")).display().to_string();

    // 1. Convert every piece on its own.
    let mut converted = vec![];
    let mut sources = vec![];
    for (i, seg) in segments.iter().enumerate() {
        match remux_for_tv(seg) {
            Ok(mp4) => {
                converted.push(mp4);
                sources.push(seg.clone());
            }
            Err(e) => step(report, "convert", FAILED, &format!("piece {} of {} ({}) could not be converted: {e}", i + 1, segments.len(), name(seg))),
        }
    }
    if converted.is_empty() {
        step(report, "convert", FAILED, "no piece could be converted; originals kept");
        return None;
    }
    if converted.len() < segments.len() {
        step(report, "convert", FAILED, &format!(
            "only {} of {} pieces converted, so footage would be missing; nothing was placed in the library and all pieces were kept in {folder}",
            converted.len(), segments.len()));
        return None;
    }
    step(report, "convert", OK, &format!("converted {} piece(s) to MP4", converted.len()));

    // 2. Join the pieces.
    let mut joined = None;
    let final_file = if converted.len() == 1 {
        converted[0].clone()
    } else {
        let out = ts.with_extension("joined.mp4");
        if let Err(e) = join_mp4(&converted, &out) {
            step(report, "join", FAILED, &format!(
                "joining {} pieces failed ({e}); nothing was placed in the library, all pieces kept in {folder}", converted.len()));
            return None;
        }
        step(report, "join", OK, &format!("joined {} pieces (provider dropout)", converted.len()));
        joined = Some(out.clone());
        out
    };

    // 3. Length check against what was planned.
    let actual = media_seconds(&final_file);
    report.set_result(json!({ "final_seconds": actual }));
    match actual {
        None => step(report, "length", WARNING, "could not measure the finished file's length"),
        Some(a) => report.event("length", length_status(a, planned_seconds), &length_text(a, planned_seconds),
            json!({ "actual_seconds": a.round() as i64, "planned_seconds": planned_seconds })),
    }

    // 4. Place in the library.
    let target = next_episode_path(show_dir, show_name, air_date);
    if let Err(e) = target.parent().map_or(Ok(()), std::fs::create_dir_all).and_then(|_| move_file(&final_file, &target)) {
        step(report, "place", FAILED, &format!("could not place the episode in the library: {e}; originals kept"));
        return None;
    }
    report.event("place", OK, &format!("placed episode at {}", target.display()), json!({ "path": target }));
    report.set_result(json!({ "episode_path": target }));

    // The episode is safe: clean up the pieces that went into it.
    for leftover in sources.iter().chain(&converted).chain(joined.iter()) {
        if leftover != &target {
            let _ = std::fs::remove_file(leftover);
        }
    }

    // 5. Tell Jellyfin.
    match crate::notify::jellyfin_scan(cfg) {
        Ok(()) => step(report, "jellyfin", OK, "Jellyfin library scan started"),
        Err(why) => step(report, "jellyfin", WARNING, &format!("{why}; the episode is in the library but was not scanned")),
    }
    Some(target)
}

fn step(report: &mut Report, step: &str, status: &'static str, message: &str) {
    println!("Post-processing [{step}]: {message}");
    report.event(step, status, message, json!({}));
}

fn name(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("kdtv-test-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn pieces_come_in_attempt_order_without_empty_ones() {
        let dir = temp_dir("segments");
        for (f, bytes) in [("show.ts", 5), ("show.part10.ts", 5), ("show.part2.ts", 5), ("show.part3.ts", 0), ("other.part2.ts", 5)] {
            std::fs::write(dir.join(f), vec![0u8; bytes]).unwrap();
        }
        let names: Vec<_> = find_segments(&dir.join("show.ts")).iter().map(|p| name(p)).collect();
        assert_eq!(names, ["show.ts", "show.part2.ts", "show.part10.ts"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn episodes_are_numbered_per_year() {
        let dir = temp_dir("episodes");
        let day = NaiveDate::from_ymd_opt(2026, 10, 21).unwrap();
        assert_eq!(
            next_episode_path(&dir, "Occupation Double", day),
            dir.join("Season 2026/Occupation Double - S2026E001 - 2026-10-21.mp4")
        );
        std::fs::create_dir_all(dir.join("Season 2026")).unwrap();
        std::fs::write(dir.join("Season 2026/a.mp4"), b"x").unwrap();
        std::fs::write(dir.join("Season 2026/a.nfo"), b"x").unwrap();
        assert!(next_episode_path(&dir, "OD", day).ends_with("OD - S2026E002 - 2026-10-21.mp4"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn length_bands_match_python() {
        assert_eq!(length_status(2099.0, 2100), OK);
        assert_eq!(length_status(1600.0, 2100), WARNING);
        assert_eq!(length_status(1000.0, 2100), FAILED);
        assert_eq!(length_text(2099.1, 2100), "34m59s recorded of 35m00s planned (100%)");
    }
}
