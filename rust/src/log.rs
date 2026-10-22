//! kd's own event log: one JSON object per line in `data/logs/kd/kd-YYYY-MM-DD.jsonl`.
//!
//! Every line is passed through the provider redaction before it is written, so a
//! stream URL that slips into a message still never lands on disk with the login.
//! Logging never fails the command that logs.

use crate::config::Config;
use serde_json::{json, Value};
use std::io::Write;

pub fn event(cfg: &Config, event: &str, fields: Value) {
    let now = chrono::Local::now();
    let mut line = json!({ "ts": now.to_rfc3339_opts(chrono::SecondsFormat::Secs, false), "event": event });
    if let (Some(line), Value::Object(fields)) = (line.as_object_mut(), fields) {
        line.extend(fields);
    }
    let dir = cfg.data_dir.join("logs").join("kd");
    let path = dir.join(format!("kd-{}.jsonl", now.format("%Y-%m-%d")));
    let text = cfg.provider.redact(&line.to_string());
    let _ = std::fs::create_dir_all(&dir).and_then(|_| {
        let mut file = std::fs::OpenOptions::new().create(true).append(true).open(&path)?;
        writeln!(file, "{text}")
    });
}

/// The last `n` events from today's log, oldest first (for the Status panel).
pub fn recent(cfg: &Config, n: usize) -> Vec<Value> {
    let path = cfg.data_dir.join("logs/kd").join(format!("kd-{}.jsonl", chrono::Local::now().format("%Y-%m-%d")));
    let text = std::fs::read_to_string(path).unwrap_or_default();
    let lines: Vec<&str> = text.lines().collect();
    lines[lines.len().saturating_sub(n)..].iter().filter_map(|l| serde_json::from_str(l).ok()).collect()
}
