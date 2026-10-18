//! The download queue for movies and series episodes.
//!
//! Each job is a small file in `data/kd/downloads/<id>.json` (no provider URL in
//! it: the worker looks the stream up when it starts). One worker, the systemd user
//! unit `kd-downloads` (`kd download-worker`), takes the jobs one at a time and
//! exits when the queue is empty.
//!
//! The provider allows a single connection, so the worker steps aside whenever a
//! recording or a restream is running and resumes the file where it stopped.

use crate::config::Config;
use crate::db::{Db, Episode, Movie};
use crate::systemd;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
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
    Episode { episode_id: String },
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Job {
    pub id: String,
    pub title: String,
    pub source: Source,
    pub output: String,
    pub state: State,
    #[serde(default)]
    pub bytes: u64,
    #[serde(default)]
    pub error: Option<String>,
    pub created_at: String,
    #[serde(default)]
    pub finished_at: Option<String>,
}

fn queue_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("kd/downloads")
}

pub fn list(cfg: &Config) -> Vec<Job> {
    let Ok(entries) = std::fs::read_dir(queue_dir(cfg)) else { return vec![] };
    let mut jobs: Vec<Job> = entries
        .filter_map(Result::ok)
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

// ── adding and cancelling ────────────────────────────────────────────────

pub fn add_movie(cfg: &Config, movie: &Movie) -> Result<Job> {
    let output = cfg.downloads_dir().join(format!("{}.{}", safe_name(&movie.name), movie.extension()));
    add(cfg, movie.name.clone(), Source::Movie { stream_id: movie.stream_id }, output)
}

pub fn add_episodes(cfg: &Config, series_name: &str, episodes: &[Episode]) -> Result<Vec<Job>> {
    let folder = cfg.downloads_dir().join(safe_name(series_name));
    episodes
        .iter()
        .map(|e| {
            let code = format!("S{:02}E{:02}", e.season, e.episode_num);
            let output = folder.join(format!("{}_{code}.{}", safe_name(series_name), e.extension));
            add(cfg, format!("{series_name} {code} {}", e.title), Source::Episode { episode_id: e.episode_id.clone() }, output)
        })
        .collect()
}

fn add(cfg: &Config, title: String, source: Source, output: PathBuf) -> Result<Job> {
    if list(cfg).iter().any(|j| j.source == source && matches!(j.state, State::Queued | State::Downloading | State::Waiting)) {
        bail!("{title} is already in the download queue");
    }
    let now = chrono::Local::now();
    let job = Job {
        id: format!("{}-{:04}", now.format("%Y%m%d%H%M%S%3f"), std::process::id() % 10000),
        title,
        source,
        output: output.display().to_string(),
        state: State::Queued,
        bytes: 0,
        error: None,
        created_at: now.format("%Y-%m-%dT%H:%M:%S").to_string(),
        finished_at: None,
    };
    save(cfg, &job)?;
    crate::log::event(cfg, "download.queued", json!({ "id": job.id, "title": job.title, "output": job.output }));
    ensure_worker(cfg)?;
    Ok(job)
}

pub fn cancel(cfg: &Config, id: &str) -> Result<Job> {
    let mut job = load(cfg, id).with_context(|| format!("no download {id}"))?;
    if matches!(job.state, State::Done | State::Failed | State::Cancelled) {
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
    let exe = std::env::current_exe().context("finding the kd program")?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.push("download-worker".into());
    systemd::run(systemd::Run {
        unit: UNIT,
        description: "KD download queue",
        working_dir: &cfg.cli_dir,
        max_seconds: None,
        command,
    })
}

// ── the worker ───────────────────────────────────────────────────────────

/// `kd download-worker`: runs inside the `kd-downloads` unit until the queue is empty.
pub fn worker(cfg: &Config) -> Result<()> {
    crate::log::event(cfg, "download.worker", json!({ "state": "started" }));
    loop {
        // Oldest first; a job still marked downloading was interrupted, so it resumes.
        let mut pending: Vec<Job> = list(cfg)
            .into_iter()
            .filter(|j| matches!(j.state, State::Queued | State::Downloading | State::Waiting))
            .collect();
        pending.sort_by(|a, b| a.id.cmp(&b.id));
        let Some(mut job) = pending.into_iter().next() else { break };

        if connection_busy(cfg) {
            if job.state != State::Waiting {
                job.state = State::Waiting;
                save(cfg, &job)?;
            }
            std::thread::sleep(Duration::from_secs(20));
            continue;
        }
        if let Err(e) = download(cfg, &mut job) {
            job.state = State::Failed;
            job.error = Some(cfg.provider.redact(&format!("{e:#}")));
            job.finished_at = Some(chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string());
            save(cfg, &job)?;
            crate::log::event(cfg, "download.failed", json!({ "id": job.id, "error": job.error }));
        }
    }
    crate::log::event(cfg, "download.worker", json!({ "state": "queue empty" }));
    Ok(())
}

fn connection_busy(cfg: &Config) -> bool {
    !systemd::active_units(crate::record::UNIT_PATTERNS).is_empty() || crate::restream::active(&cfg.data_dir).is_some()
}

/// Download one job with curl, resuming a partial file. Returns when the job is
/// done, cancelled, or put back to wait for the connection.
fn download(cfg: &Config, job: &mut Job) -> Result<()> {
    let url = {
        let db = Db::open(&cfg.data_dir)?;
        match &job.source {
            Source::Movie { stream_id } => db.movie_url(*stream_id)?,
            Source::Episode { episode_id } => db.episode_url(episode_id)?,
        }
        .context("the provider no longer lists this title")?
    };
    let output = PathBuf::from(&job.output);
    let part = PathBuf::from(format!("{}.part", job.output));
    if let Some(dir) = output.parent() {
        std::fs::create_dir_all(dir)?;
    }

    job.state = State::Downloading;
    job.error = None;
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

    loop {
        std::thread::sleep(Duration::from_secs(3));
        job.bytes = file_size(&part);
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
            job.state = State::Done;
            job.finished_at = Some(chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string());
            save(cfg, job)?;
            crate::log::event(cfg, "download.done", json!({ "id": job.id, "bytes": job.bytes, "output": job.output }));
            return Ok(());
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
            save(cfg, job)?;
            crate::log::event(cfg, "download.paused", json!({ "id": job.id, "bytes": job.bytes }));
            return Ok(());
        }
        save(cfg, job)?;
    }
}

fn file_size(p: &Path) -> u64 {
    std::fs::metadata(p).map(|m| m.len()).unwrap_or(0)
}

/// Like `downloads._safe_name`: letters, digits, spaces, dashes; spaces become `_`.
pub fn safe_name(name: &str) -> String {
    let kept: String = name.chars().filter(|c| c.is_alphanumeric() || matches!(c, ' ' | '-' | '_')).collect();
    kept.split_whitespace().collect::<Vec<_>>().join("_")
}
