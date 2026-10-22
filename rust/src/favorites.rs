//! My list: the channels, movies and series you saved, from `data/favorites.json`
//! (the runtime list). Each entry has a `type` ("live", "movie" or "series", "live"
//! when missing) and the provider's id in `stream_id` (the series id for a series).
//! Saving keeps every other field of the entries already there.

use anyhow::{Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::Path;

#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
pub enum Kind {
    Live,
    Movie,
    Series,
}

impl Kind {
    fn word(self) -> &'static str {
        match self {
            Kind::Live => "live",
            Kind::Movie => "movie",
            Kind::Series => "series",
        }
    }

    fn from_word(word: Option<&str>) -> Option<Kind> {
        match word.unwrap_or("live") {
            "live" => Some(Kind::Live),
            "movie" => Some(Kind::Movie),
            "series" => Some(Kind::Series),
            _ => None,
        }
    }
}

/// One saved entry, in the order it was added.
#[derive(Clone, Debug, PartialEq)]
pub struct Saved {
    pub kind: Kind,
    pub id: i64,
    pub name: String,
    pub category: Option<String>,
}

#[derive(Deserialize)]
struct Entry {
    stream_id: Value,
    name: Option<String>,
    category_name: Option<String>,
    #[serde(rename = "type")]
    kind: Option<String>,
}

/// Saved as "414054" by older versions and 414054 by newer ones.
fn id_of(value: &Value) -> Option<i64> {
    value.as_i64().or_else(|| value.as_str()?.parse().ok())
}

fn read(data_dir: &Path) -> Vec<Value> {
    std::fs::read_to_string(data_dir.join("favorites.json")).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
}

pub fn list(data_dir: &Path) -> Vec<Saved> {
    read(data_dir)
        .into_iter()
        .filter_map(|v| {
            let e: Entry = serde_json::from_value(v).ok()?;
            Some(Saved { kind: Kind::from_word(e.kind.as_deref())?, id: id_of(&e.stream_id)?, name: e.name.unwrap_or_default(), category: e.category_name })
        })
        .collect()
}

/// Add the entry, or take it out when it's already saved. True when it was added.
/// The first save keeps a copy of the list as it was in `favorites.json.bak`.
pub fn toggle(data_dir: &Path, item: &Saved) -> Result<bool> {
    let path = data_dir.join("favorites.json");
    let backup = data_dir.join("favorites.json.bak");
    if path.exists() && !backup.exists() {
        std::fs::copy(&path, &backup).with_context(|| format!("backing up {}", path.display()))?;
    }
    let mut entries = read(data_dir);
    let same = |v: &Value| {
        Kind::from_word(v["type"].as_str()) == Some(item.kind) && id_of(&v["stream_id"]) == Some(item.id)
    };
    let before = entries.len();
    entries.retain(|v| !same(v));
    let added = entries.len() == before;
    if added {
        entries.push(json!({
            "stream_id": item.id,
            "name": item.name,
            "type": item.kind.word(),
            "category_name": item.category,
            "added": chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
        }));
    }
    let tmp = data_dir.join("favorites.json.tmp");
    std::fs::write(&tmp, serde_json::to_string_pretty(&entries)? + "\n").with_context(|| format!("writing {}", tmp.display()))?;
    std::fs::rename(&tmp, &path).with_context(|| format!("saving {}", path.display()))?;
    Ok(added)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adding_and_removing_keeps_the_other_entries() {
        let dir = std::env::temp_dir().join(format!("kdtv-favorites-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("favorites.json"),
            r#"[{"stream_id": "414054", "name": "CA EN: CRAVE1 HD", "stream_url": "http://x/1.ts", "type": "live", "category_name": "CA| GENERAL EN"}]"#,
        )
        .unwrap();

        let movie = Saved { kind: Kind::Movie, id: 414054, name: "Below".into(), category: None };
        assert!(toggle(&dir, &movie).unwrap());
        let saved = list(&dir);
        assert_eq!(saved.len(), 2);
        assert_eq!(saved[0], Saved { kind: Kind::Live, id: 414054, name: "CA EN: CRAVE1 HD".into(), category: Some("CA| GENERAL EN".into()) });
        assert_eq!(saved[1].kind, Kind::Movie);
        assert!(std::fs::read_to_string(dir.join("favorites.json")).unwrap().contains("http://x/1.ts"));
        assert!(dir.join("favorites.json.bak").exists());

        assert!(!toggle(&dir, &movie).unwrap());
        assert_eq!(list(&dir).len(), 1);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
