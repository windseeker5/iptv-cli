//! "Update the list now": runs the same script as the hourly timer
//! (`scripts/refresh_tv_data.py`), with `--guide` for the full TV guide.

use crate::config::Config;
use anyhow::{bail, Context, Result};
use serde_json::{json, Value};
use std::process::Command;

pub fn run(cfg: &Config, guide: bool) -> Result<String> {
    let mut cmd = Command::new(cfg.python());
    cmd.arg(cfg.cli_dir.join("scripts/refresh_tv_data.py")).current_dir(&cfg.cli_dir);
    if guide {
        cmd.arg("--guide");
    }
    let started = std::time::Instant::now();
    let out = cmd.output().context("running the refresh script")?;
    let summary: Value = serde_json::from_slice(&out.stdout).unwrap_or(Value::Null);
    crate::log::event(cfg, "refresh", json!({ "guide": guide, "ok": out.status.success(), "seconds": started.elapsed().as_secs() }));
    if !out.status.success() {
        let detail = summary["error"].as_str().map(str::to_string).unwrap_or_else(|| String::from_utf8_lossy(&out.stderr).lines().last().unwrap_or("").to_string());
        bail!("the provider didn't answer: {}", cfg.provider.redact(&detail));
    }
    let what = if guide { "Channel list and TV guide" } else { "Channel list" };
    Ok(format!("{what} updated in {} s", started.elapsed().as_secs()))
}
