//! YouTube search through yt-dlp. Downloads go through the download queue
//! (`downloads::add_youtube`), which runs yt-dlp too.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::process::Command;

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Video {
    pub id: String,
    pub title: String,
    pub channel: String,
    /// Seconds; 0 for live streams.
    pub duration: i64,
    pub url: String,
}

pub fn search(query: &str, count: usize) -> Result<Vec<Video>> {
    if query.trim().is_empty() {
        return Ok(vec![]);
    }
    let out = Command::new("yt-dlp")
        .args(["--flat-playlist", "-J", "--no-warnings", "--socket-timeout", "20"])
        .arg(format!("ytsearch{count}:{}", query.trim()))
        .output()
        .context("running yt-dlp (is it installed?)")?;
    if !out.status.success() {
        bail!("YouTube search failed: {}", String::from_utf8_lossy(&out.stderr).lines().last().unwrap_or("").trim());
    }
    let data: serde_json::Value = serde_json::from_slice(&out.stdout).context("yt-dlp sent something that isn't JSON")?;
    Ok(data["entries"]
        .as_array()
        .map(|entries| {
            entries
                .iter()
                .filter_map(|e| {
                    let id = e["id"].as_str()?.to_string();
                    Some(Video {
                        title: e["title"].as_str().unwrap_or("?").to_string(),
                        channel: e["channel"].as_str().or(e["uploader"].as_str()).unwrap_or("").to_string(),
                        duration: e["duration"].as_f64().unwrap_or(0.0) as i64,
                        url: format!("https://www.youtube.com/watch?v={id}"),
                        id,
                    })
                })
                .collect()
        })
        .unwrap_or_default())
}

/// 4022 → "1:07:02", 850 → "14:10"
pub fn duration(seconds: i64) -> String {
    let (h, m, s) = (seconds / 3600, seconds % 3600 / 60, seconds % 60);
    if seconds <= 0 {
        "live".into()
    } else if h > 0 {
        format!("{h}:{m:02}:{s:02}")
    } else {
        format!("{m}:{s:02}")
    }
}
