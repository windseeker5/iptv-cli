//! Open something in mpv, detached from kd: a live channel, a movie, an episode
//! (all over the provider connection) or a file on disk.
//!
//! While mpv plays from the provider, a file named after its process id sits in
//! `data/kd/playing/`, so provider downloads wait for it to close.

use crate::config::Config;
use anyhow::{bail, Context, Result};
use serde_json::json;
use std::path::Path;
use std::process::{Command, Stdio};

pub enum Target<'a> {
    Live(i64),
    /// A provider URL straight from the database (movie or episode), or a YouTube link.
    Url(&'a str),
    File(&'a std::path::Path),
}

pub fn play(cfg: &Config, title: &str, target: Target) -> Result<()> {
    let source = match target {
        Target::Live(stream_id) => {
            if cfg.provider.server.is_empty() {
                bail!("no provider login: IPTV_SERVER_URL is not set (check cli_dir in kd.toml)");
            }
            cfg.provider.stream_url(stream_id)
        }
        Target::Url(url) => url.to_string(),
        Target::File(path) => path.display().to_string(),
    };
    let mut child = Command::new("mpv")
        .arg(format!("--force-media-title={title}"))
        .arg("--really-quiet")
        .arg(&source)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("starting mpv")?;
    let from_provider = !cfg.provider.server.is_empty() && source.starts_with(&cfg.provider.server);
    let marker = playing_dir(cfg).join(child.id().to_string());
    if from_provider {
        let _ = std::fs::create_dir_all(playing_dir(cfg));
        let _ = std::fs::write(&marker, title);
    }
    // Reap mpv when it closes so the TUI doesn't collect zombies.
    std::thread::spawn(move || {
        let _ = child.wait();
        if from_provider {
            let _ = std::fs::remove_file(&marker);
        }
    });
    crate::log::event(cfg, "play", json!({ "title": title, "source": cfg.provider.redact(&source) }));
    Ok(())
}

fn playing_dir(cfg: &Config) -> std::path::PathBuf {
    cfg.data_dir.join("kd/playing")
}

/// Whether mpv is playing something from the provider right now. A file left
/// behind by a kdtv that closed first counts only while its mpv still runs.
pub fn playing(cfg: &Config) -> bool {
    let Ok(entries) = std::fs::read_dir(playing_dir(cfg)) else { return false };
    let mut any = false;
    for entry in entries.flatten() {
        let alive = entry.file_name().to_str().is_some_and(|pid| Path::new("/proc").join(pid).exists());
        if alive {
            any = true;
        } else {
            let _ = std::fs::remove_file(entry.path());
        }
    }
    any
}
