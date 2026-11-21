//! Small helpers around `systemctl --user` and `systemd-run --user`.
//!
//! At home on Linux, everything long-running (recordings, the restream, the
//! download queue) runs as a systemd user unit (see `jobs.rs`), so it keeps going
//! after kdtv exits and can be stopped by name.

use anyhow::{bail, Context, Result};
use std::process::{Command, Output};

pub fn systemctl(args: &[&str]) -> Result<Output> {
    Command::new("systemctl").arg("--user").args(args).output().context("running systemctl")
}

pub fn is_active(unit: &str) -> bool {
    systemctl(&["is-active", "--quiet", unit]).is_ok_and(|o| o.status.success())
}

/// Running, or a oneshot still "activating" (a scheduled recording mid-run).
pub fn is_running(unit: &str) -> bool {
    systemctl(&["show", unit, "--property=ActiveState", "--value"])
        .is_ok_and(|o| matches!(String::from_utf8_lossy(&o.stdout).trim(), "active" | "activating" | "deactivating"))
}

/// Running units matching the patterns, e.g. `kdcli-rec-*.service`. A oneshot
/// service (the scheduled recordings) stays "activating" for its whole run, so
/// that counts as running too.
pub fn active_units(patterns: &[&str]) -> Vec<String> {
    let mut args = vec!["list-units", "--state=active,activating", "--plain", "--no-legend"];
    args.extend_from_slice(patterns);
    systemctl(&args)
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter_map(|l| l.split_whitespace().next().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// A transient unit, started right away.
pub fn run(r: crate::jobs::Run) -> Result<()> {
    let mut cmd = Command::new("systemd-run");
    cmd.args(["--user", "--collect", "--quiet"])
        .arg(format!("--unit={}", r.unit))
        .arg(format!("--description={}", r.description))
        .arg(format!("--working-directory={}", r.working_dir.display()));
    if let Some(max) = r.max_seconds {
        cmd.arg(format!("--property=RuntimeMaxSec={max}"));
    }
    let out = cmd.args(&r.command).output().context("running systemd-run (systemd is required)")?;
    if !out.status.success() {
        bail!("systemd refused to start {}: {}", r.unit, String::from_utf8_lossy(&out.stderr).trim());
    }
    Ok(())
}
