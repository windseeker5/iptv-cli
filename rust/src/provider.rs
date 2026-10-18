//! Calls to the provider's player API. Only series episode lists for now; the
//! channel list and guide are still downloaded by the Python timers.
//!
//! Uses curl, like the downloads, so kd carries no TLS stack of its own.

use crate::config::Config;
use crate::db::{Db, EpisodeRow};
use anyhow::{bail, Context, Result};
use serde_json::Value;
use std::process::Command;

const BROWSER_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

/// Download a series' episode list and cache it in the database. Returns the count.
pub fn fetch_episodes(cfg: &Config, series_id: i64) -> Result<usize> {
    let p = &cfg.provider;
    if p.server.is_empty() || p.username.is_empty() {
        bail!("no provider login (IPTV_SERVER_URL / IPTV_USERNAME in .env)");
    }
    let out = Command::new("curl")
        .args(["-sS", "-G", "--fail", "--max-time", "60", "-A", BROWSER_UA, "-H", "Accept: application/json"])
        .arg(format!("{}/player_api.php", p.server))
        .args(["--data-urlencode", &format!("username={}", p.username)])
        .args(["--data-urlencode", &format!("password={}", p.password)])
        .args(["--data-urlencode", "action=get_series_info"])
        .args(["--data-urlencode", &format!("series_id={series_id}")])
        .output()
        .context("running curl")?;
    if !out.status.success() {
        bail!("provider: {}", p.redact(String::from_utf8_lossy(&out.stderr).trim()));
    }
    let data: Value = serde_json::from_slice(&out.stdout).context("provider sent something that isn't JSON")?;
    let rows = episode_rows(cfg, series_id, &data);
    if rows.is_empty() {
        bail!("the provider lists no episodes for this series");
    }
    Db::open_rw(&cfg.data_dir)?.cache_episodes(&rows)?;
    crate::log::event(cfg, "series.episodes", serde_json::json!({ "series_id": series_id, "count": rows.len() }));
    Ok(rows.len())
}

fn episode_rows(cfg: &Config, series_id: i64, data: &Value) -> Vec<EpisodeRow> {
    let p = &cfg.provider;
    let now = chrono::Utc::now().timestamp();
    let text = |v: &Value| match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    };
    let int = |v: &Value| v.as_i64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok())).unwrap_or(0);
    let Some(seasons) = data.get("episodes").and_then(Value::as_object) else { return vec![] };
    seasons
        .values()
        .filter_map(Value::as_array)
        .flatten()
        .filter_map(|ep| {
            let id = text(ep.get("id")?);
            let info = ep.get("info").cloned().unwrap_or(Value::Null);
            let ext = Some(text(ep.get("container_extension").unwrap_or(&Value::Null))).filter(|e| !e.is_empty()).unwrap_or_else(|| "mp4".into());
            Some(EpisodeRow {
                stream_url: format!("{}/series/{}/{}/{id}.{ext}", p.server, p.username, p.password),
                episode_id: id,
                series_id,
                season: int(ep.get("season").unwrap_or(&Value::Null)),
                season_name: text(info.get("season_name").unwrap_or(&Value::Null)),
                episode_num: int(ep.get("episode_num").unwrap_or(&Value::Null)),
                title: text(ep.get("title").unwrap_or(&Value::Null)),
                extension: ext,
                air_date: text(info.get("air_date").unwrap_or(&Value::Null)),
                duration: text(info.get("duration").unwrap_or(&Value::Null)),
                duration_secs: int(info.get("duration_secs").unwrap_or(&Value::Null)),
                rating: info.get("rating").and_then(|r| r.as_f64().or_else(|| r.as_str()?.parse().ok())),
                added: text(ep.get("added").unwrap_or(&Value::Null)),
                direct_source: text(ep.get("direct_source").unwrap_or(&Value::Null)),
                cached_at: now,
            })
        })
        .collect()
}
