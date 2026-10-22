//! Writing the run diary: one JSON report per recording in `data/runs/<run_id>.json`
//! (`runs.rs` reads them back). The recorder adds an event for every step: start,
//! each attempt and dropout, join, length check, placement, Jellyfin, Discord.
//! The report is saved after every event, so a crash still leaves the story up to
//! that point. JP reads these instead of FFmpeg progress logs.
//!
//! Never put secrets in a report: URLs are cut and the provider login is redacted.

use crate::config::Config;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

pub const OK: &str = "ok";
pub const WARNING: &str = "warning";
pub const FAILED: &str = "failed";
const RUNNING: &str = "running";
/// A busy provider can mean hundreds of empty retries; the first are enough.
const MAX_EMPTY_ATTEMPTS: usize = 20;

fn rank(status: &str) -> u8 {
    match status {
        FAILED => 2,
        WARNING => 1,
        _ => 0,
    }
}

pub struct Report {
    path: PathBuf,
    pub data: Map<String, Value>,
    status: &'static str,
    cfg: Config,
}

pub struct Attempt<'a> {
    pub number: u32,
    pub file: &'a Path,
    pub planned_seconds: i64,
    pub captured_seconds: f64,
    pub size_mb: f64,
    pub ok: bool,
    pub wrote: bool,
    pub error: Option<&'a str>,
}

impl Report {
    /// Start a report; `meta` goes in as top-level keys (channel_name, stream_id…).
    pub fn new(cfg: &Config, run_id: &str, meta: Value) -> Report {
        let mut data = Map::new();
        data.insert("run_id".into(), run_id.into());
        data.insert("status".into(), RUNNING.into());
        data.insert("started_at".into(), now_iso().into());
        data.insert("finished_at".into(), Value::Null);
        if let Value::Object(meta) = meta {
            data.extend(meta);
        }
        data.insert("attempts".into(), json!([]));
        data.insert("events".into(), json!([]));
        data.insert("result".into(), json!({}));
        let report = Report { path: cfg.data_dir.join("runs").join(format!("{run_id}.json")), data, status: OK, cfg: cfg.clone() };
        report.save();
        report
    }

    pub fn run_id(&self) -> &str {
        self.data["run_id"].as_str().unwrap_or("")
    }

    /// Add one step. `status` is OK, WARNING or FAILED; `extra` adds fields to the event.
    pub fn event(&mut self, step: &str, status: &'static str, message: &str, extra: Value) {
        if rank(status) > rank(self.status) {
            self.status = status;
        }
        let mut e = json!({
            "time": chrono::Local::now().format("%H:%M:%S").to_string(),
            "step": step,
            "status": status,
            "message": self.clean(message),
        });
        if let (Some(e), Value::Object(extra)) = (e.as_object_mut(), extra) {
            e.extend(extra.into_iter().filter(|(_, v)| !v.is_null()));
        }
        self.push("events", e);
    }

    pub fn attempt(&mut self, a: Attempt) {
        let empty = self.list("attempts").iter().filter(|x| x["wrote_data"] == false).count();
        if !a.wrote && empty >= MAX_EMPTY_ATTEMPTS {
            return;
        }
        let entry = json!({
            "attempt": a.number,
            "file": a.file.file_name().map(|n| n.to_string_lossy().to_string()),
            "planned_seconds": a.planned_seconds,
            "captured_seconds": a.captured_seconds.round() as i64,
            "size_mb": (a.size_mb * 10.0).round() / 10.0,
            "ok": a.ok,
            "wrote_data": a.wrote,
            "error": a.error.map(|e| self.clean(e)),
        });
        self.push("attempts", entry);
    }

    pub fn set_result(&mut self, fields: Value) {
        if let (Some(Value::Object(result)), Value::Object(fields)) = (self.data.get_mut("result"), fields) {
            result.extend(fields);
        }
        self.save();
    }

    pub fn result(&self, key: &str) -> &Value {
        self.data.get("result").and_then(|r| r.get(key)).unwrap_or(&Value::Null)
    }

    pub fn status(&self) -> &'static str {
        self.status
    }

    pub fn list(&self, key: &str) -> &[Value] {
        self.data.get(key).and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[])
    }

    /// Warning and failure messages, in order.
    pub fn problems(&self) -> Vec<String> {
        self.list("events")
            .iter()
            .filter(|e| e["status"] == WARNING || e["status"] == FAILED)
            .filter_map(|e| e["message"].as_str().map(str::to_string))
            .collect()
    }

    pub fn finish(&mut self) {
        self.data.insert("status".into(), self.status.into());
        self.data.insert("finished_at".into(), now_iso().into());
        self.save();
    }

    fn push(&mut self, key: &str, value: Value) {
        if let Some(Value::Array(list)) = self.data.get_mut(key) {
            list.push(value);
        }
        self.save();
    }

    fn clean(&self, text: &str) -> String {
        cut_urls(&self.cfg.provider.redact(text)).chars().take(400).collect()
    }

    /// Write through a temporary file so a reader never sees half a report.
    /// The diary must never break a recording, so errors are ignored.
    fn save(&self) {
        let Some(dir) = self.path.parent() else { return };
        let tmp = dir.join(format!(".{}.tmp", self.run_id()));
        let Ok(text) = serde_json::to_string_pretty(&self.data) else { return };
        let _ = std::fs::create_dir_all(dir)
            .and_then(|_| std::fs::write(&tmp, text))
            .and_then(|_| std::fs::rename(&tmp, &self.path));
    }
}

fn now_iso() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

/// "error at http://x/y/z.ts here" → "error at <url> here".
fn cut_urls(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = ["http://", "https://"].iter().filter_map(|p| rest.find(p)).min() {
        out.push_str(&rest[..at]);
        out.push_str("<url>");
        let tail = &rest[at..];
        rest = &tail[tail.find(char::is_whitespace).unwrap_or(tail.len())..];
    }
    out.push_str(rest);
    out
}

#[cfg(test)]
mod tests {
    use super::cut_urls;

    #[test]
    fn urls_never_reach_the_diary() {
        assert_eq!(cut_urls("Error opening http://h:80/live/u/p/1.ts: 458"), "Error opening <url> 458");
        assert_eq!(cut_urls("a https://x b http://y"), "a <url> b <url>");
        assert_eq!(cut_urls("no link"), "no link");
    }
}
