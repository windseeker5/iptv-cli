//! Reading the run diary: one report per recording in `data/runs/<run_id>.json`,
//! written by the recorder (`diary.rs`).

use serde::{Deserialize, Serialize};
use std::path::Path;

#[derive(Deserialize)]
struct RunFile {
    run_id: String,
    status: Option<String>,
    channel_name: Option<String>,
    started_at: Option<String>,
    planned_seconds: Option<f64>,
    show_name: Option<String>,
    #[serde(default)]
    result: RunResult,
    #[serde(default)]
    events: Vec<RunEvent>,
}

#[derive(Deserialize, Default)]
struct RunResult {
    final_seconds: Option<f64>,
    captured_seconds: Option<f64>,
    episode_path: Option<String>,
}

#[derive(Deserialize)]
struct RunEvent {
    status: Option<String>,
    message: Option<String>,
}

/// One recording, with the same keys JP gets from `recent_recordings` today.
#[derive(Serialize)]
pub struct Run {
    pub id: String,
    pub channel_name: Option<String>,
    pub show_name: Option<String>,
    pub start_time: Option<String>,
    pub planned: String,
    pub recorded: String,
    pub status: String,
    pub problems: Vec<String>,
    pub episode_path: Option<String>,
}

/// Newest first.
pub fn recent(data_dir: &Path, limit: usize) -> Vec<Run> {
    let Ok(entries) = std::fs::read_dir(data_dir.join("runs")) else {
        return vec![];
    };
    let mut files: Vec<_> = entries
        .filter_map(Result::ok)
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    files.sort_by_key(|(modified, _)| std::cmp::Reverse(*modified));
    // A run still marked running with no recording unit alive was stopped or crashed.
    let recording_now = !crate::systemd::active_units(crate::record::UNIT_PATTERNS).is_empty();
    files
        .iter()
        .take(limit)
        .filter_map(|(_, p)| serde_json::from_str::<RunFile>(&std::fs::read_to_string(p).ok()?).ok())
        .map(|r| Run {
            id: r.run_id,
            channel_name: r.channel_name,
            show_name: r.show_name,
            start_time: r.started_at,
            planned: format_seconds(r.planned_seconds),
            recorded: format_seconds(r.result.final_seconds.or(r.result.captured_seconds)),
            status: match r.status.as_deref() {
                Some("running") if !recording_now => "stopped".into(),
                Some(s) => s.to_string(),
                None => "?".into(),
            },
            problems: r
                .events
                .into_iter()
                .filter(|e| matches!(e.status.as_deref(), Some("warning" | "failed")))
                .filter_map(|e| e.message)
                .collect(),
            episode_path: r.result.episode_path,
        })
        .collect()
}

/// 2099.1 → "34m59s", 9000 → "2h30m00s", like `run_report.format_seconds`.
pub fn format_seconds(seconds: Option<f64>) -> String {
    let Some(seconds) = seconds else { return "?".into() };
    let s = seconds.round() as i64;
    let (h, m, sec) = (s / 3600, s % 3600 / 60, s % 60);
    if h > 0 {
        format!("{h}h{m:02}m{sec:02}s")
    } else {
        format!("{m}m{sec:02}s")
    }
}

#[cfg(test)]
mod tests {
    use super::format_seconds;

    #[test]
    fn formats_like_python() {
        assert_eq!(format_seconds(None), "?");
        assert_eq!(format_seconds(Some(2099.1137)), "34m59s");
        assert_eq!(format_seconds(Some(9000.0)), "2h30m00s");
        assert_eq!(format_seconds(Some(59.6)), "1m00s");
    }
}
