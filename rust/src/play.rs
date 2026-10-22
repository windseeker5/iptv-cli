//! Open something in mpv, detached from kd: a live channel, a movie, an episode
//! (all over the provider connection) or a file on disk.

use crate::config::Config;
use anyhow::{bail, Context, Result};
use serde_json::json;
use std::process::{Command, Stdio};

pub enum Target<'a> {
    Live(i64),
    /// A provider URL straight from the database (movie or episode).
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
    // Reap mpv when it closes so the TUI doesn't collect zombies.
    std::thread::spawn(move || child.wait());
    crate::log::event(cfg, "play", json!({ "title": title, "source": cfg.provider.redact(&source) }));
    Ok(())
}
