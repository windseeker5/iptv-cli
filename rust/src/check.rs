//! `kdtv check`: is this machine ready? For setting up a laptop or a Pi.
//! Each line says what's fine, what's missing, and what to do about it.

use crate::config::{Config, RestreamVia};
use anyhow::Result;
use serde::Serialize;
use std::process::Command;

#[derive(Serialize)]
struct Item {
    what: String,
    ok: bool,
    /// Needed for kdtv to work at all (as opposed to one feature).
    required: bool,
    detail: String,
}

fn tool(name: &str, version_arg: &str) -> Option<String> {
    let out = Command::new(name).arg(version_arg).output().ok()?;
    let text = String::from_utf8_lossy(if out.stdout.is_empty() { &out.stderr } else { &out.stdout }).into_owned();
    Some(text.lines().next().unwrap_or("").chars().take(60).collect())
}

pub fn run(cfg: &Config, json: bool) -> Result<()> {
    let mut items = vec![];
    let mut add = |what: &str, ok, required, detail: String| items.push(Item { what: what.to_string(), ok, required, detail });

    add("Config file", cfg.file.is_some(), false, match &cfg.file {
        Some(f) => f.display().to_string(),
        None => "none found: using the current folder (see rust/kdtv.example.toml)".into(),
    });
    add("CLI folder", cfg.cli_dir.join("scripts/record_scheduled.py").is_file(), true, cfg.cli_dir.display().to_string());
    add("Python recorder", cfg.python().is_file(), true, if cfg.python().is_file() {
        cfg.python().display().to_string()
    } else {
        format!("no {}: python3 -m venv venv && venv/bin/pip install -r requirements.txt", cfg.python().display())
    });
    let p = &cfg.provider;
    let login = !p.server.is_empty() && !p.username.is_empty() && !p.password.is_empty();
    add("Provider login", login, true, if login { format!("in {}", cfg.env_file.display()) } else { format!("set IPTV_SERVER_URL, IPTV_USERNAME, IPTV_PASSWORD in {}", cfg.env_file.display()) });

    let db = crate::db::Db::open(&cfg.data_dir).ok().and_then(|d| d.counts().ok());
    add("Channel list", db.as_ref().is_some_and(|c| c.live > 0), true, match &db {
        Some(c) if c.live > 0 => format!("{} channels, {} movies, {} series", c.live, c.vod, c.series),
        _ => "empty: run kdtv refresh --everything".into(),
    });
    let (_, guide_age) = crate::status::list_age_minutes(&cfg.data_dir);
    add("TV guide", guide_age.is_some_and(|m| m < 48 * 60), false, match guide_age {
        Some(m) => format!("updated {} h ago", m / 60),
        None => "never downloaded: run kdtv refresh --guide".into(),
    });

    let records = crate::record::records_dir(cfg);
    add("Recordings folder", records.as_ref().is_ok_and(|(_, w)| w.is_none()), false, match records {
        Ok((dir, None)) => dir.display().to_string(),
        Ok((dir, Some(w))) => format!("{w} (using {})", dir.display()),
        Err(e) => e.to_string(),
    });

    for (name, arg, required, why) in [
        ("ffmpeg", "-version", true, "records and restreams"),
        ("curl", "--version", true, "talks to the provider and downloads"),
        ("mpv", "--version", false, "plays channels, movies and files"),
        ("yt-dlp", "--version", false, "YouTube search and downloads"),
        ("systemctl", "--version", true, "runs recordings, the restream and downloads in the background"),
        ("docker", "--version", false, "only for the NGINX restream and other containers"),
    ] {
        let found = tool(name, arg);
        add(name, found.is_some(), required, match found {
            Some(v) => v,
            None => format!("not installed ({why})"),
        });
    }

    let linger = Command::new("loginctl").args(["show-user", &std::env::var("USER").unwrap_or_default(), "-p", "Linger", "--value"]).output();
    let linger = linger.is_ok_and(|o| String::from_utf8_lossy(&o.stdout).trim() == "yes");
    add("Runs when logged out", linger, false, if linger { "yes (user lingering on)".into() } else { "no: sudo loginctl enable-linger $USER, so timers record while you're logged out".into() });

    let restream = match cfg.restream_via {
        RestreamVia::Nginx => "through the NGINX container".to_string(),
        RestreamVia::Kdtv => format!("through kdtv on port {} ({})", cfg.web_port, if crate::web::port_in_use(cfg) { "port in use: kdtv web or another server answers" } else { "free" }),
    };
    add("Restream", true, false, restream);

    if json {
        println!("{}", serde_json::to_string_pretty(&items)?);
        return Ok(());
    }
    let mut missing_required = false;
    for i in &items {
        let mark = if i.ok { "\x1b[32m✓\x1b[0m" } else if i.required { "\x1b[31m✗\x1b[0m" } else { "\x1b[33m!\x1b[0m" };
        missing_required |= !i.ok && i.required;
        println!("{mark} {:<22} {}", i.what, i.detail);
    }
    println!();
    println!("{}", if missing_required { "Some required parts are missing (✗)." } else { "Ready." });
    Ok(())
}
