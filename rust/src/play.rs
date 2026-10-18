//! Open a live channel in mpv, detached from kd.

use crate::config::Config;
use crate::db::Channel;
use anyhow::{bail, Context, Result};
use serde_json::json;
use std::process::{Command, Stdio};

pub fn play(cfg: &Config, channel: &Channel) -> Result<()> {
    if cfg.provider.server.is_empty() {
        bail!("no provider login: IPTV_SERVER_URL is not set (check env_file in kd.toml)");
    }
    let mut child = Command::new("mpv")
        .arg(format!("--force-media-title={}", channel.name))
        .arg("--really-quiet")
        .arg(cfg.provider.stream_url(channel.stream_id))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .context("starting mpv")?;
    // Reap mpv when it closes so the TUI doesn't collect zombies.
    std::thread::spawn(move || child.wait());
    crate::log::event(cfg, "play", json!({ "stream_id": channel.stream_id, "channel": channel.name }));
    Ok(())
}
