//! Recording schedules, read from `data/schedules/<id>.json`.
//!
//! Same files the Python side writes (`iptv_tui/domain/schedules.py`); the systemd
//! user timer `kdcli-rec-<id>.timer` says when each one fires next.

use serde::{Deserialize, Serialize};
use std::path::Path;
use std::process::Command;

#[derive(Deserialize)]
struct ScheduleFile {
    id: String,
    stream_id: i64,
    channel_name: String,
    time: String,
    duration_min: i64,
    #[serde(default)]
    days: Vec<String>,
    date: Option<String>,
    show_name: Option<String>,
    #[serde(default = "yes")]
    enabled: bool,
    created_by: Option<String>,
}

fn yes() -> bool {
    true
}

/// One schedule, with the same keys JP gets from `list_schedules` today.
#[derive(Serialize)]
pub struct Schedule {
    pub id: String,
    pub what: String,
    pub enabled: bool,
    pub next_run: Option<String>,
    pub stream_id: i64,
    pub channel_name: String,
    pub days: Vec<String>,
    pub date: Option<String>,
    pub time: String,
    pub duration_min: i64,
    pub show_name: Option<String>,
    pub created_by: Option<String>,
}

pub fn list(data_dir: &Path) -> Vec<Schedule> {
    let Ok(entries) = std::fs::read_dir(data_dir.join("schedules")) else {
        return vec![];
    };
    let mut files: Vec<_> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    files.sort();
    files
        .iter()
        .filter_map(|p| serde_json::from_str::<ScheduleFile>(&std::fs::read_to_string(p).ok()?).ok())
        .map(|s| Schedule {
            what: describe(&s),
            next_run: next_run(&s.id),
            id: s.id,
            enabled: s.enabled,
            stream_id: s.stream_id,
            channel_name: s.channel_name,
            days: s.days,
            date: s.date,
            time: s.time,
            duration_min: s.duration_min,
            show_name: s.show_name,
            created_by: s.created_by,
        })
        .collect()
}

/// The description of one schedule, for naming the recording a timer is running.
pub fn describe_id(data_dir: &Path, id: &str) -> Option<String> {
    let text = std::fs::read_to_string(data_dir.join("schedules").join(format!("{id}.json"))).ok()?;
    Some(describe(&serde_json::from_str(&text).ok()?))
}

/// "CA FR: NOOVO V-TELE HD, every Sun at 18:29, 152 min → Jellyfin “Occupation Double”"
fn describe(s: &ScheduleFile) -> String {
    let when = if s.days.is_empty() {
        format!("on {}", s.date.as_deref().unwrap_or("?"))
    } else {
        format!("every {}", s.days.join("/"))
    };
    let into = s.show_name.as_ref().map(|n| format!(" → Jellyfin “{n}”")).unwrap_or_default();
    format!("{}, {} at {}, {} min{}", s.channel_name, when, s.time, s.duration_min, into)
}

fn next_run(id: &str) -> Option<String> {
    let out = Command::new("systemctl")
        .args(["--user", "show", &format!("kdcli-rec-{id}.timer"), "--property=NextElapseUSecRealtime", "--value"])
        .output()
        .ok()?;
    let value = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (!value.is_empty()).then_some(value)
}
