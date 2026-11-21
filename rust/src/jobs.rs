//! Background jobs: recordings, the download queue, the restream. Each one has a
//! name (`kdcli-now-…`, `kd-downloads`…) and keeps going after kdtv closes.
//!
//! Two ways to run them:
//! - systemd user units (`systemd.rs`): Linux at home, with timers for schedules;
//! - kdtv's own hidden processes, one small file per job in `data/jobs/<name>.json`
//!   (its process id and what it is): Windows, and the "lite" profile anywhere.
//!   Schedules then run from inside kdtv while it is open (`schedules::start_due`).

use crate::config::Config;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;

enum Backend {
    Systemd,
    Processes(PathBuf),
}

static BACKEND: OnceLock<Backend> = OnceLock::new();

/// Pick how jobs run, once, right after the config is read.
pub fn init(cfg: &Config) {
    let _ = BACKEND.set(if cfg.lite { Backend::Processes(cfg.data_dir.join("jobs")) } else { Backend::Systemd });
}

fn backend() -> &'static Backend {
    BACKEND.get_or_init(|| if cfg!(windows) { Backend::Processes(std::env::temp_dir().join("kdtv-jobs")) } else { Backend::Systemd })
}

/// True at home on Linux: systemd runs the jobs and the schedules' timers.
pub fn uses_systemd() -> bool {
    matches!(backend(), Backend::Systemd)
}

/// The kdtv program to start a job with. When kdtv was reinstalled while this one
/// runs, Linux names the old file "… (deleted)": the new one at the same path is
/// the one to start.
pub fn kdtv_exe() -> Result<PathBuf> {
    let exe = std::env::current_exe().context("finding the kdtv program")?;
    let text = exe.display().to_string();
    match text.strip_suffix(" (deleted)").map(PathBuf::from) {
        Some(installed) if installed.exists() => Ok(installed),
        _ => Ok(exe),
    }
}

/// A job to start right away.
pub struct Run<'a> {
    pub unit: &'a str,
    pub description: &'a str,
    pub working_dir: &'a Path,
    /// systemd stops the job after this long (kdtv's own jobs end by themselves).
    pub max_seconds: Option<u64>,
    pub command: Vec<String>,
}

pub fn start(r: Run) -> Result<()> {
    match backend() {
        Backend::Systemd => crate::systemd::run(r),
        Backend::Processes(dir) => start_process(dir, r),
    }
}

pub fn is_active(unit: &str) -> bool {
    match backend() {
        Backend::Systemd => crate::systemd::is_active(unit),
        Backend::Processes(dir) => read_alive(dir, unit).is_some(),
    }
}

/// Running, or a scheduled recording mid-run.
pub fn is_running(unit: &str) -> bool {
    match backend() {
        Backend::Systemd => crate::systemd::is_running(unit),
        Backend::Processes(dir) => read_alive(dir, unit).is_some(),
    }
}

/// Running jobs whose name matches one of the patterns (`kdcli-rec-*.service`),
/// named the way systemd names them (`….service`).
pub fn active_units(patterns: &[&str]) -> Vec<String> {
    match backend() {
        Backend::Systemd => crate::systemd::active_units(patterns),
        Backend::Processes(dir) => {
            let Ok(entries) = std::fs::read_dir(dir) else { return vec![] };
            let mut names: Vec<String> = entries
                .filter_map(Result::ok)
                .filter_map(|e| e.file_name().to_str()?.strip_suffix(".json").map(str::to_string))
                .map(|name| format!("{name}.service"))
                .filter(|unit| patterns.iter().any(|p| glob(p, unit)))
                .filter(|unit| read_alive(dir, unit).is_some())
                .collect();
            names.sort();
            names
        }
    }
}

/// Stop a job and what it started.
pub fn stop(unit: &str) -> Result<()> {
    match backend() {
        Backend::Systemd => {
            let out = crate::systemd::systemctl(&["stop", unit])?;
            if !out.status.success() {
                bail!("could not stop {unit}: {}", String::from_utf8_lossy(&out.stderr).trim());
            }
            Ok(())
        }
        Backend::Processes(dir) => {
            let Some(job) = read_alive(dir, unit) else { return Ok(()) };
            crate::os::terminate_tree(job.pid);
            for _ in 0..50 {
                if !crate::os::alive(job.pid) {
                    break;
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            let _ = std::fs::remove_file(file(dir, unit));
            Ok(())
        }
    }
}

/// What a running job is ("KDTV recording: …").
pub fn description(unit: &str) -> Option<String> {
    match backend() {
        Backend::Systemd => {
            let out = crate::systemd::systemctl(&["show", unit, "--property=Description", "--value"]).ok()?;
            Some(String::from_utf8_lossy(&out.stdout).trim().to_string()).filter(|t| !t.is_empty())
        }
        Backend::Processes(dir) => read_alive(dir, unit).map(|j| j.description),
    }
}

// ── kdtv's own processes ─────────────────────────────────────────────────

#[derive(Serialize, Deserialize)]
struct JobFile {
    pid: u32,
    description: String,
    started_at: String,
}

fn bare(unit: &str) -> &str {
    unit.strip_suffix(".service").unwrap_or(unit)
}

fn file(dir: &Path, unit: &str) -> PathBuf {
    dir.join(format!("{}.json", bare(unit)))
}

/// The job's file while its process runs; a file left by a finished job is removed.
fn read_alive(dir: &Path, unit: &str) -> Option<JobFile> {
    let path = file(dir, unit);
    let job: JobFile = serde_json::from_str(&std::fs::read_to_string(&path).ok()?).ok()?;
    if crate::os::alive(job.pid) {
        Some(job)
    } else {
        let _ = std::fs::remove_file(&path);
        None
    }
}

fn start_process(dir: &Path, r: Run) -> Result<()> {
    if read_alive(dir, r.unit).is_some() {
        bail!("{} is already running", r.unit);
    }
    let (program, args) = r.command.split_first().context("empty job command")?;
    std::fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    // What the job prints goes to a log next to its file, for when something goes wrong.
    let log = std::fs::File::create(dir.join(format!("{}.log", bare(r.unit)))).ok();
    let mut cmd = Command::new(program);
    cmd.args(args).current_dir(r.working_dir).stdin(Stdio::null());
    match log.as_ref().and_then(|l| Some((l.try_clone().ok()?, l.try_clone().ok()?))) {
        Some((out, err)) => cmd.stdout(out).stderr(err),
        None => cmd.stdout(Stdio::null()).stderr(Stdio::null()),
    };
    crate::os::detach(&mut cmd);
    let mut child = cmd.spawn().with_context(|| format!("starting {}", r.unit))?;
    let job = JobFile { pid: child.id(), description: r.description.to_string(), started_at: chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string() };
    std::fs::write(file(dir, r.unit), serde_json::to_string_pretty(&job)?)?;
    // Collect the job when it ends, so it doesn't linger as a zombie while kdtv runs.
    std::thread::spawn(move || {
        let _ = child.wait();
    });
    Ok(())
}

/// `*` matches any run of characters: "kdcli-rec-*.service".
fn glob(pattern: &str, text: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern == text,
        Some((head, rest)) => {
            let Some(tail) = text.strip_prefix(head) else { return false };
            if rest.is_empty() {
                return true;
            }
            (0..=tail.len()).filter(|&i| tail.is_char_boundary(i)).any(|i| glob(rest, &tail[i..]))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn patterns_match_like_systemd() {
        assert!(glob("kdcli-rec-*.service", "kdcli-rec-ab12-noovo.service"));
        assert!(glob("kdcli-now-*.service", "kdcli-now-20261121-180000.service"));
        assert!(!glob("kdcli-rec-*.service", "kdcli-now-1.service"));
        assert!(!glob("kdcli-rec-*.service", "kdcli-rec-1.timer"));
        assert!(glob("kd-downloads", "kd-downloads"));
    }

    #[test]
    fn own_processes_start_list_and_stop() {
        let dir = std::env::temp_dir().join(format!("kdtv-jobs-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        #[cfg(unix)]
        let command = vec!["sh".to_string(), "-c".into(), "echo hello; sleep 30".into()];
        #[cfg(windows)]
        let command = vec!["cmd".to_string(), "/C".into(), "echo hello & ping -n 30 127.0.0.1 >NUL".into()];
        start_process(&dir, Run { unit: "kdcli-now-test", description: "a test", working_dir: &std::env::temp_dir(), max_seconds: None, command: command.clone() })
            .unwrap();
        assert!(read_alive(&dir, "kdcli-now-test.service").is_some());
        // The same job can't run twice.
        assert!(start_process(&dir, Run { unit: "kdcli-now-test", description: "again", working_dir: &std::env::temp_dir(), max_seconds: None, command }).is_err());
        let job = read_alive(&dir, "kdcli-now-test").unwrap();
        assert_eq!(job.description, "a test");
        let log_file = dir.join("kdcli-now-test.log");
        for _ in 0..50 {
            if std::fs::read_to_string(&log_file).is_ok_and(|l| l.contains("hello")) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        crate::os::terminate_tree(job.pid);
        for _ in 0..50 {
            if !crate::os::alive(job.pid) {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        assert!(read_alive(&dir, "kdcli-now-test").is_none());
        assert!(!file(&dir, "kdcli-now-test").exists(), "the file of a finished job is cleaned up");
        let log = std::fs::read_to_string(&log_file).unwrap();
        assert!(log.contains("hello"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
