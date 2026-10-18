//! One snapshot of everything worth knowing at a glance: services, what is
//! recording or restreaming right now, and how fresh the channel list and guide are.

use crate::config::Config;
use crate::db::{Account, Counts, Db};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::Path;
use std::process::Command;

#[derive(Serialize)]
pub struct Status {
    pub profile: String,
    pub services: Vec<Service>,
    pub recording: Vec<Recording>,
    pub restream: Restream,
    pub list_age_minutes: Option<i64>,
    pub guide_age_minutes: Option<i64>,
    pub database: Option<Counts>,
    pub account: Option<Account>,
}

#[derive(Serialize)]
pub struct Service {
    pub name: String,
    /// "up", "stopped", "missing" or "unknown" (Docker not reachable)
    pub state: String,
    pub detail: String,
}

#[derive(Serialize)]
pub struct Recording {
    pub unit: String,
    pub what: String,
}

#[derive(Serialize, Default)]
pub struct Restream {
    pub live: bool,
    pub channel_name: Option<String>,
    pub since: Option<String>,
}

impl Status {
    /// True when starting playback would take the provider connection a recording needs.
    pub fn connection_busy(&self) -> bool {
        let max = self.account.as_ref().map_or(1, |a| a.max_connections).max(1) as usize;
        self.recording.len() + usize::from(self.restream.live) >= max
    }
}

pub fn collect(cfg: &Config, db: Option<&Db>) -> Status {
    let ages = refresh_ages(&cfg.data_dir);
    Status {
        profile: cfg.profile.clone(),
        services: services(&cfg.containers),
        recording: active_recordings(&cfg.data_dir),
        restream: restream(&cfg.data_dir),
        list_age_minutes: ages.get("channels").copied(),
        guide_age_minutes: ages.get("guide").copied(),
        database: db.and_then(|d| d.counts().ok()),
        account: db.and_then(|d| d.account().ok().flatten()),
    }
}

fn services(names: &[String]) -> Vec<Service> {
    let listing = Command::new("docker")
        .args(["ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned());
    let known: HashMap<&str, &str> = listing
        .as_deref()
        .unwrap_or("")
        .lines()
        .filter_map(|l| l.split_once('\t'))
        .collect();
    names
        .iter()
        .map(|name| {
            let (state, detail) = match (&listing, known.get(name.as_str())) {
                (None, _) => ("unknown", "Docker not reachable".to_string()),
                (_, None) => ("missing", "no such container".to_string()),
                (_, Some(s)) if s.starts_with("Up") => ("up", s.to_string()),
                (_, Some(s)) => ("stopped", s.to_string()),
            };
            Service { name: name.clone(), state: state.into(), detail }
        })
        .collect()
}

/// Recording services systemd is running right now (scheduled and NOOVO jobs).
fn active_recordings(data_dir: &Path) -> Vec<Recording> {
    let Ok(out) = Command::new("systemctl")
        .args(["--user", "list-units", "--state=active", "--plain", "--no-legend", "kdcli-rec-*.service", "iptv-noovo-record*.service"])
        .output()
    else {
        return vec![];
    };
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter_map(|l| l.split_whitespace().next())
        .map(|unit| {
            let id = unit.strip_prefix("kdcli-rec-").and_then(|u| u.strip_suffix(".service"));
            let what = id
                .and_then(|id| crate::schedules::describe_id(data_dir, id))
                .unwrap_or_else(|| unit.trim_end_matches(".service").to_string());
            Recording { unit: unit.to_string(), what }
        })
        .collect()
}

#[derive(Deserialize)]
struct RestreamFile {
    channel_name: Option<String>,
    pid: Option<u32>,
    started_at: Option<String>,
}

/// From `data/.restream_active.json`, live only while its FFmpeg is still running.
/// (The file also holds the source URL; it is never read into kd.)
fn restream(data_dir: &Path) -> Restream {
    let file: Option<RestreamFile> = std::fs::read_to_string(data_dir.join(".restream_active.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok());
    match file {
        Some(f) if f.pid.is_some_and(|pid| Path::new(&format!("/proc/{pid}")).exists()) => {
            Restream { live: true, channel_name: f.channel_name, since: f.started_at }
        }
        _ => Restream::default(),
    }
}

/// Minutes since the channel list and the full guide were downloaded (`data/cache/refresh.json`).
fn refresh_ages(data_dir: &Path) -> HashMap<String, i64> {
    let stamps: HashMap<String, f64> = std::fs::read_to_string(data_dir.join("cache/refresh.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    let now = chrono::Utc::now().timestamp() as f64;
    stamps.into_iter().map(|(k, ts)| (k, ((now - ts) / 60.0) as i64)).collect()
}

pub fn list_age_minutes(data_dir: &Path) -> (Option<i64>, Option<i64>) {
    let ages = refresh_ages(data_dir);
    (ages.get("channels").copied(), ages.get("guide").copied())
}
