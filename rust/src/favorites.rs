//! Favorite live channels, from `data/favorites.json` (the runtime list the Python TUI keeps).

use crate::db::Channel;
use serde::Deserialize;
use std::path::Path;

#[derive(Deserialize)]
struct Favorite {
    stream_id: serde_json::Value,
    name: Option<String>,
    category_name: Option<String>,
    #[serde(rename = "type")]
    kind: Option<String>,
}

pub fn live(data_dir: &Path) -> Vec<Channel> {
    let favorites: Vec<Favorite> = std::fs::read_to_string(data_dir.join("favorites.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    favorites
        .into_iter()
        .filter(|f| f.kind.as_deref().unwrap_or("live") == "live")
        .filter_map(|f| {
            // Saved as "414054" by older versions and 414054 by newer ones.
            let stream_id = f.stream_id.as_i64().or_else(|| f.stream_id.as_str()?.parse().ok())?;
            Some(Channel { stream_id, name: f.name.unwrap_or_default(), category: f.category_name })
        })
        .collect()
}
