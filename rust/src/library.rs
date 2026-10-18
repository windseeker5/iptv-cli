//! Finished media on disk: recordings, downloads, YouTube and the Jellyfin TV shows.

use crate::config::Config;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::time::UNIX_EPOCH;

const MEDIA_EXTENSIONS: &[&str] = &["avi", "flv", "m4v", "mkv", "mov", "mp3", "mp4", "ts", "webm"];

#[derive(Serialize, Clone)]
pub struct Media {
    pub kind: &'static str,
    pub name: String,
    pub path: PathBuf,
    pub size: u64,
    /// Seconds since 1970.
    pub modified: i64,
}

pub fn roots(cfg: &Config) -> Vec<(&'static str, PathBuf)> {
    // Both the recordings folder and its fallback, for when the USB drive was unplugged.
    let mut roots = vec![("Recording", cfg.records_dir()), ("Recording", cfg.data_dir.join("recordings"))];
    roots.push(("Download", cfg.downloads_dir()));
    roots.push(("YouTube", cfg.data_dir.join("youtube")));
    if let Some(tv) = cfg.tv_library_root() {
        roots.push(("TV show", tv));
    }
    let mut seen = vec![];
    roots.retain(|(_, p)| {
        let key = p.canonicalize().unwrap_or_else(|_| p.clone());
        let new = !seen.contains(&key);
        seen.push(key);
        new
    });
    roots
}

/// Newest first.
pub fn list(cfg: &Config) -> Vec<Media> {
    let mut out = vec![];
    for (kind, root) in roots(cfg) {
        walk(&root, &mut |path| {
            let ext = path.extension().and_then(|e| e.to_str()).map(str::to_lowercase);
            if !ext.is_some_and(|e| MEDIA_EXTENSIONS.contains(&e.as_str())) {
                return;
            }
            let Ok(meta) = path.metadata() else { return };
            out.push(Media {
                kind,
                name: path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
                path: path.to_path_buf(),
                size: meta.len(),
                modified: meta.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map_or(0, |d| d.as_secs() as i64),
            });
        });
    }
    out.sort_by_key(|m| std::cmp::Reverse(m.modified));
    out
}

fn walk(dir: &Path, f: &mut dyn FnMut(&Path)) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.filter_map(Result::ok) {
        let path = entry.path();
        match entry.file_type() {
            Ok(t) if t.is_dir() => walk(&path, f),
            Ok(t) if t.is_file() => f(&path),
            _ => {}
        }
    }
}

/// 3_500_000_000 → "3.3 GB"
pub fn human_size(bytes: u64) -> String {
    let units = ["B", "KB", "MB", "GB", "TB"];
    let mut size = bytes as f64;
    let mut unit = 0;
    while size >= 1024.0 && unit < units.len() - 1 {
        size /= 1024.0;
        unit += 1;
    }
    if unit == 0 { format!("{bytes} B") } else { format!("{size:.1} {}", units[unit]) }
}
