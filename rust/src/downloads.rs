//! The download queue: movies, series episodes and YouTube videos.
//!
//! Each job is a small file in `data/kd/downloads/<id>.json` (no provider URL in
//! it: the worker looks the stream up when it starts). One worker, the systemd user
//! unit `kd-downloads` (`kdtv download-worker`), takes the jobs one at a time and
//! exits when the queue is empty.
//!
//! The provider allows a single connection, so provider downloads step aside
//! whenever a recording, a restream or something played from the provider runs
//! (see `play::playing`) and resume the file where they stopped. YouTube downloads
//! don't use that connection and carry on. Each finished or failed download shows
//! a desktop notification.
//!
//! Files go to `downloads_dir` in kdtv.toml, else USB_DOWNLOADS_PATH, else
//! `data/downloads`: movies at the top, a folder per series.

use crate::config::Config;
use crate::db::{Db, Episode, Movie};
use crate::systemd;
use crate::youtube::Video;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

const UNIT: &str = "kd-downloads";
const USER_AGENT: &str = "VLC/3.0.0 LibVLC/3.0.0";

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
#[serde(rename_all = "lowercase")]
pub enum State {
    Queued,
    Downloading,
    /// Waiting for a recording or restream to free the provider connection.
    Waiting,
    Done,
    Failed,
    Cancelled,
}

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Source {
    Movie { stream_id: i64 },
    /// `series_id` lets the worker ask the provider again when the link is gone.
    Episode {
        episode_id: String,
        #[serde(default)]
        series_id: Option<i64>,
    },
    YouTube { url: String, audio: bool },
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Job {
    pub id: String,
    pub title: String,
    pub source: Source,
    /// The file; for YouTube, the folder until yt-dlp names the file.
    pub output: String,
    pub state: State,
    #[serde(default)]
    pub bytes: u64,
    #[serde(default)]
    pub total: Option<u64>,
    #[serde(default)]
    pub percent: Option<f64>,
    /// Bytes a second, smoothed; only while downloading.
    #[serde(default)]
    pub speed: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub finished_at: Option<String>,
}

impl Job {
    pub fn unfinished(&self) -> bool {
        matches!(self.state, State::Queued | State::Downloading | State::Waiting)
    }

    /// 0–100 when known. A size smaller than what already arrived is not the real one.
    pub fn progress(&self) -> Option<f64> {
        self.percent.or_else(|| self.real_total().map(|t| self.bytes as f64 * 100.0 / t as f64))
    }

    /// The file's size, when the provider told us a believable one.
    pub fn real_total(&self) -> Option<u64> {
        self.total.filter(|t| *t > 1_000_000 && *t >= self.bytes)
    }

    /// Seconds to go at the current speed.
    pub fn seconds_left(&self) -> Option<i64> {
        let (total, speed) = (self.real_total()?, self.speed.filter(|s| *s > 0.0)?);
        Some((total.saturating_sub(self.bytes) as f64 / speed) as i64)
    }

    pub fn needs_provider(&self) -> bool {
        !matches!(self.source, Source::YouTube { .. })
    }
}

impl Source {
    /// The same title, whatever extra the job file carries.
    fn same(&self, other: &Source) -> bool {
        match (self, other) {
            (Source::Episode { episode_id: a, .. }, Source::Episode { episode_id: b, .. }) => a == b,
            _ => self == other,
        }
    }
}

fn queue_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("kd/downloads")
}

/// Newest first.
pub fn list(cfg: &Config) -> Vec<Job> {
    let Ok(entries) = std::fs::read_dir(queue_dir(cfg)) else { return vec![] };
    let mut jobs: Vec<Job> = entries
        .filter_map(Result::ok)
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| serde_json::from_str(&std::fs::read_to_string(e.path()).ok()?).ok())
        .collect();
    jobs.sort_by(|a, b| b.id.cmp(&a.id));
    jobs
}

fn save(cfg: &Config, job: &Job) -> Result<()> {
    let dir = queue_dir(cfg);
    std::fs::create_dir_all(&dir)?;
    let tmp = dir.join(format!(".{}.tmp", job.id));
    std::fs::write(&tmp, serde_json::to_string_pretty(job)?)?;
    std::fs::rename(&tmp, dir.join(format!("{}.json", job.id)))?;
    Ok(())
}

fn load(cfg: &Config, id: &str) -> Option<Job> {
    serde_json::from_str(&std::fs::read_to_string(queue_dir(cfg).join(format!("{id}.json"))).ok()?).ok()
}

// ── where files go ───────────────────────────────────────────────────────

pub fn movie_output(cfg: &Config, movie: &Movie) -> PathBuf {
    cfg.downloads_dir().join(format!("{}.{}", safe_name(&movie.name), movie.extension()))
}

/// `downloads/<Series>/<Series>_S01E02.mkv`, the same layout as the old TUI.
pub fn episode_output(cfg: &Config, series_name: &str, e: &Episode) -> PathBuf {
    let code = format!("S{:02}E{:02}", e.season, e.episode_num);
    cfg.downloads_dir().join(safe_name(series_name)).join(format!("{}_{code}.{}", safe_name(series_name), e.extension))
}

pub fn youtube_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("youtube")
}

// ── adding and cancelling ────────────────────────────────────────────────

/// Make sure the folder exists and kdtv may write in it, before anything is queued.
fn check_folder(dir: &Path) -> Result<()> {
    std::fs::create_dir_all(dir).with_context(|| format!("can't create the download folder {}", dir.display()))?;
    let probe = dir.join(".kdtv-write-test");
    std::fs::write(&probe, b"").with_context(|| format!("kdtv isn't allowed to save in {}", dir.display()))?;
    let _ = std::fs::remove_file(&probe);
    Ok(())
}

pub fn add_movie(cfg: &Config, movie: &Movie) -> Result<Job> {
    check_folder(&cfg.downloads_dir())?;
    let job = add(cfg, movie.name.clone(), Source::Movie { stream_id: movie.stream_id }, movie_output(cfg, movie))?;
    ensure_worker(cfg)?;
    Ok(job)
}

/// Queue episodes, skipping the ones already on disk or in the queue.
pub fn add_episodes(cfg: &Config, series_name: &str, episodes: &[Episode]) -> Result<Vec<Job>> {
    check_folder(&cfg.downloads_dir())?;
    let queued = list(cfg);
    let mut jobs = vec![];
    for e in episodes {
        let source = Source::Episode { episode_id: e.episode_id.clone(), series_id: Some(e.series_id) };
        let output = episode_output(cfg, series_name, e);
        if output.exists() || queued.iter().any(|j| j.source.same(&source) && j.unfinished()) {
            continue;
        }
        let code = format!("S{:02}E{:02}", e.season, e.episode_num);
        jobs.push(add(cfg, format!("{series_name} {code}"), source, output)?);
    }
    if jobs.is_empty() {
        bail!("those episodes are already downloaded or in the queue");
    }
    ensure_worker(cfg)?;
    Ok(jobs)
}

pub fn add_youtube(cfg: &Config, video: &Video, audio: bool) -> Result<Job> {
    let job = add(cfg, video.title.clone(), Source::YouTube { url: video.url.clone(), audio }, youtube_dir(cfg))?;
    ensure_worker(cfg)?;
    Ok(job)
}

fn add(cfg: &Config, title: String, source: Source, output: PathBuf) -> Result<Job> {
    if list(cfg).iter().any(|j| j.source.same(&source) && j.unfinished()) {
        bail!("{title} is already in the download queue");
    }
    // Unique and in order, even when a whole series is queued in one go.
    static SEQ: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
    let now = chrono::Local::now();
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let job = Job {
        id: format!("{}-{:05}-{:04}", now.format("%Y%m%d%H%M%S"), seq, std::process::id() % 10000),
        title,
        source,
        output: output.display().to_string(),
        state: State::Queued,
        bytes: 0,
        total: None,
        percent: None,
        speed: None,
        error: None,
        created_at: now.format("%Y-%m-%dT%H:%M:%S").to_string(),
        finished_at: None,
    };
    save(cfg, &job)?;
    crate::log::event(cfg, "download.queued", json!({ "id": job.id, "title": job.title }));
    Ok(job)
}

/// Take the done, failed and cancelled downloads off the list (files on disk stay).
pub fn clear_finished(cfg: &Config) -> Result<usize> {
    let finished: Vec<Job> = list(cfg).into_iter().filter(|j| !j.unfinished()).collect();
    for j in &finished {
        std::fs::remove_file(queue_dir(cfg).join(format!("{}.json", j.id)))?;
    }
    crate::log::event(cfg, "download.clear", json!({ "count": finished.len() }));
    Ok(finished.len())
}

pub fn cancel(cfg: &Config, id: &str) -> Result<Job> {
    let mut job = load(cfg, id).with_context(|| format!("no download {id}"))?;
    if !job.unfinished() {
        bail!("{} is already {:?}", job.title, job.state);
    }
    job.state = State::Cancelled;
    save(cfg, &job)?;
    crate::log::event(cfg, "download.cancel", json!({ "id": id }));
    Ok(job)
}

/// Start the worker unless it is already running.
fn ensure_worker(cfg: &Config) -> Result<()> {
    if systemd::is_active(UNIT) {
        return Ok(());
    }
    let exe = systemd::kdtv_exe()?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.push("download-worker".into());
    systemd::run(systemd::Run { unit: UNIT, description: "KDTV download queue", working_dir: &cfg.cli_dir, max_seconds: None, command })
}

// ── the worker ───────────────────────────────────────────────────────────

/// `kdtv download-worker`: runs inside the `kd-downloads` unit until the queue is empty.
pub fn worker(cfg: &Config) -> Result<()> {
    crate::log::event(cfg, "download.worker", json!({ "state": "started" }));
    loop {
        // Oldest first; a job still marked downloading was interrupted, so it resumes.
        let mut pending: Vec<Job> = list(cfg).into_iter().filter(Job::unfinished).collect();
        if pending.is_empty() {
            break;
        }
        pending.sort_by(|a, b| a.id.cmp(&b.id));
        let busy = connection_busy(cfg);
        let Some(mut job) = pending.iter().find(|j| !busy || !j.needs_provider()).cloned() else {
            // Only provider downloads left and the connection is taken: wait.
            for mut j in pending.into_iter().filter(|j| j.state != State::Waiting) {
                j.state = State::Waiting;
                save(cfg, &j)?;
            }
            std::thread::sleep(Duration::from_secs(20));
            continue;
        };
        let result = match &job.source {
            Source::YouTube { url, audio } => {
                let (url, audio) = (url.clone(), *audio);
                download_youtube(cfg, &mut job, &url, audio)
            }
            _ => download(cfg, &mut job),
        };
        if let Err(e) = result {
            job.state = State::Failed;
            job.error = Some(cfg.provider.redact(&format!("{e:#}")));
            job.finished_at = Some(stamp());
            save(cfg, &job)?;
            crate::log::event(cfg, "download.failed", json!({ "id": job.id, "error": job.error }));
            crate::notify::desktop(&format!("Download failed: {}", job.title), job.error.as_deref().unwrap_or(""), true);
        }
    }
    crate::log::event(cfg, "download.worker", json!({ "state": "queue empty" }));
    Ok(())
}

fn stamp() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

fn connection_busy(cfg: &Config) -> bool {
    !systemd::active_units(crate::record::UNIT_PATTERNS).is_empty() || crate::restream::active(&cfg.data_dir).is_some() || crate::play::playing(cfg)
}

fn finish(cfg: &Config, job: &mut Job) -> Result<()> {
    job.state = State::Done;
    job.percent = Some(100.0);
    job.speed = None;
    job.finished_at = Some(stamp());
    save(cfg, job)?;
    crate::log::event(cfg, "download.done", json!({ "id": job.id, "title": job.title, "bytes": job.bytes }));
    crate::notify::desktop(&format!("Downloaded {}", job.title), &format!("Saved in {}", job.output), false);
    Ok(())
}

/// The file's size, for a percentage. The provider refuses a HEAD request (502,
/// 16 bytes), so ask for the first byte and read the total in Content-Range.
fn remote_size(url: &str) -> Option<u64> {
    let out = Command::new("curl")
        .args(["-sS", "-L", "-r", "0-0", "--max-time", "20", "-A", USER_AGENT, "-H", "Accept: */*", "-D", "-", "-o", "/dev/null"])
        .arg(url)
        .output()
        .ok()?;
    size_from_headers(&String::from_utf8_lossy(&out.stdout))
}

/// "Content-Range: bytes 0-0/3638412721" → 3638412721 (the last response, after redirects).
fn size_from_headers(headers: &str) -> Option<u64> {
    headers
        .lines()
        .filter_map(|l| {
            let (name, value) = l.split_once(':')?;
            name.trim().eq_ignore_ascii_case("content-range").then(|| value.rsplit('/').next()?.trim().parse::<u64>().ok())?
        })
        .next_back()
        .filter(|n| *n > 1_000_000)
}

/// Download one provider job with curl, resuming a partial file. Returns when the
/// job is done, cancelled, or put back to wait for the connection.
fn download(cfg: &Config, job: &mut Job) -> Result<()> {
    let lookup = || -> Result<Option<String>> {
        let db = Db::open(&cfg.data_dir)?;
        match &job.source {
            Source::Movie { stream_id } => db.movie_url(*stream_id),
            Source::Episode { episode_id, .. } => db.episode_url(episode_id),
            Source::YouTube { .. } => unreachable!("YouTube jobs use yt-dlp"),
        }
    };
    let url = match (lookup()?, &job.source) {
        (Some(url), _) => Some(url),
        // The episode links were cleared (an older full refresh did that): ask again.
        (None, Source::Episode { series_id: Some(series_id), .. }) => {
            crate::provider::fetch_episodes(cfg, *series_id)?;
            lookup()?
        }
        (None, _) => None,
    }
    .context("the provider no longer lists this title")?;
    let output = PathBuf::from(&job.output);
    let part = PathBuf::from(format!("{}.part", job.output));
    if let Some(dir) = output.parent() {
        check_folder(dir)?;
    }

    job.state = State::Downloading;
    job.error = None;
    if job.real_total().is_none() {
        job.total = remote_size(&url);
    }
    save(cfg, job)?;
    crate::log::event(cfg, "download.start", json!({ "id": job.id, "title": job.title }));

    let mut child = Command::new("curl")
        .args(["-sS", "-L", "--fail", "-C", "-", "--retry", "3", "--connect-timeout", "30", "-A", USER_AGENT])
        .args(["-H", "Accept: */*", "-o"])
        .arg(&part)
        .arg(&url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .context("starting curl")?;

    job.speed = None;
    let mut last = (file_size(&part), std::time::Instant::now());
    loop {
        std::thread::sleep(Duration::from_secs(3));
        job.bytes = file_size(&part);
        let now = (job.bytes, std::time::Instant::now());
        let speed = now.0.saturating_sub(last.0) as f64 / now.1.duration_since(last.1).as_secs_f64().max(0.1);
        job.speed = Some(job.speed.map_or(speed, |s| s * 0.7 + speed * 0.3));
        last = now;
        if let Some(status) = child.try_wait()? {
            if !status.success() {
                let mut err = String::new();
                if let Some(mut e) = child.stderr.take() {
                    let _ = std::io::Read::read_to_string(&mut e, &mut err);
                }
                bail!("curl: {}", err.trim().replace(&url, "<provider-url>"));
            }
            std::fs::rename(&part, &output)?;
            job.bytes = file_size(&output);
            return finish(cfg, job);
        }
        if load(cfg, &job.id).is_some_and(|j| j.state == State::Cancelled) {
            let _ = child.kill();
            let _ = child.wait();
            let _ = std::fs::remove_file(&part);
            return Ok(());
        }
        if connection_busy(cfg) {
            let _ = child.kill();
            let _ = child.wait();
            job.state = State::Waiting;
            job.speed = None;
            save(cfg, job)?;
            crate::log::event(cfg, "download.paused", json!({ "id": job.id, "bytes": job.bytes }));
            return Ok(());
        }
        save(cfg, job)?;
    }
}

/// Download one YouTube video (or its audio) with yt-dlp, reading its progress lines.
fn download_youtube(cfg: &Config, job: &mut Job, url: &str, audio: bool) -> Result<()> {
    let dir = youtube_dir(cfg);
    std::fs::create_dir_all(&dir)?;
    job.state = State::Downloading;
    job.error = None;
    save(cfg, job)?;
    crate::log::event(cfg, "download.start", json!({ "id": job.id, "title": job.title }));

    let mut cmd = Command::new("yt-dlp");
    cmd.args(["--newline", "--no-playlist", "--socket-timeout", "30", "--extractor-args", "youtube:player_client=android,web,tv"])
        .args(["--print", "after_move:filepath", "-o"])
        .arg(dir.join("%(title)s.%(ext)s"));
    if audio {
        cmd.args(["-x", "--audio-format", "mp3"]);
    } else {
        cmd.args(["-f", "bestvideo[height<=1080]+bestaudio/best", "--merge-output-format", "mp4"]);
    }
    let mut child = cmd.arg(url).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().context("starting yt-dlp")?;

    let mut last_save = std::time::Instant::now();
    let mut final_path = None;
    if let Some(out) = child.stdout.take() {
        for line in BufReader::new(out).lines().map_while(Result::ok) {
            // "[download]  45.2% of  123.45MiB at ..." or the final file path.
            if let Some(rest) = line.strip_prefix("[download]") {
                if let Some(pct) = rest.trim().split('%').next().and_then(|p| p.trim().parse::<f64>().ok()) {
                    job.percent = Some(pct);
                }
            } else if line.starts_with('/') {
                final_path = Some(line.trim().to_string());
            }
            if last_save.elapsed() > Duration::from_secs(2) {
                if load(cfg, &job.id).is_some_and(|j| j.state == State::Cancelled) {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Ok(());
                }
                save(cfg, job)?;
                last_save = std::time::Instant::now();
            }
        }
    }
    let status = child.wait()?;
    if !status.success() {
        let mut err = String::new();
        if let Some(mut e) = child.stderr.take() {
            let _ = std::io::Read::read_to_string(&mut e, &mut err);
        }
        bail!("yt-dlp: {}", err.lines().rfind(|l| l.contains("ERROR")).unwrap_or(err.trim()));
    }
    if let Some(path) = final_path {
        job.bytes = file_size(Path::new(&path));
        job.output = path;
    }
    finish(cfg, job)
}

fn file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

/// Like `downloads._safe_name`: letters, digits, spaces, dashes; spaces become `_`.
pub fn safe_name(name: &str) -> String {
    let kept: String = name.chars().filter(|c| c.is_alphanumeric() || matches!(c, ' ' | '-' | '_')).collect();
    kept.split_whitespace().collect::<Vec<_>>().join("_")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn size_comes_from_the_last_content_range() {
        let headers = "HTTP/1.1 302 Found\r\nContent-Type: text/html\r\n\r\nHTTP/1.1 206 Partial Content\r\nContent-Length: 1\r\nContent-Range: bytes 0-0/3638412721\r\n";
        assert_eq!(size_from_headers(headers), Some(3_638_412_721));
        assert_eq!(size_from_headers("HTTP/1.1 502 Bad Gateway\r\nContent-Length: 16\r\n"), None);
    }
}
