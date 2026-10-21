//! `kdtv preflight`: the check before scheduled recordings, run every 10 minutes by
//! `kdcli-preflight.timer`. For each enabled schedule starting 40-80 minutes from
//! now (once per run) it checks what the recording needs: provider login and a
//! free connection, a short test pull of the channel, disk space, the recordings
//! drive, Jellyfin and the library folder, the Discord webhook.
//!
//! Quiet when everything is fine; Discord only when something is wrong. Every
//! check is logged to `data/preflight.jsonl`, which JP reads (system_health).

use crate::config::Config;
use crate::jp::eyes;
use anyhow::Result;
use chrono::{Local, NaiveDateTime};
use serde_json::{json, Value};
use std::io::Write;
use std::path::Path;

const WINDOW_MIN: f64 = 40.0;
const WINDOW_MAX: f64 = 80.0;
/// ~3.7 Mbit/s stream, doubled for the .ts plus the converted copy.
const GB_PER_MINUTE: f64 = 0.03;
/// The same warning is repeated at most once a day.
const REPING_HOURS: i64 = 24;
const OK: &str = "ok";
const WARN: &str = "warning";
const FAIL: &str = "failed";

/// "Thu 2026-10-22 18:29:00 EDT" (systemd's NextElapse) → 2026-10-22 18:29:00.
fn parse_next(value: &str) -> Option<NaiveDateTime> {
    let re = regex::Regex::new(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}").ok()?;
    NaiveDateTime::parse_from_str(re.find(value)?.as_str(), "%Y-%m-%d %H:%M:%S").ok()
}

fn checks_for(cfg: &Config, s: &crate::schedules::Schedule) -> Vec<(&'static str, String)> {
    let mut out = vec![];
    let acct = eyes::provider_account(cfg);
    if acct["reachable"] != true {
        out.push((FAIL, format!("the IPTV provider is not answering ({})", acct["error"].as_str().unwrap_or("no connection"))));
    } else if acct["login_ok"] != true {
        out.push((FAIL, format!("the IPTV provider refused the login (HTTP {})", acct["http"])));
    } else {
        if acct["account_status"] != "Active" {
            out.push((FAIL, format!("the IPTV account status is {}", acct["account_status"].as_str().unwrap_or("unknown"))));
        }
        if let Some(days) = acct["expires_in_days"].as_i64()
            && days < 7
        {
            out.push((if days >= 0 { WARN } else { FAIL }, format!("the IPTV subscription expires in {days} day(s)")));
        }
        let (used, allowed) = (acct["connections_in_use"].as_i64().unwrap_or(0), acct["connections_allowed"].as_i64().unwrap_or(0));
        if allowed > 0 && used >= allowed {
            out.push((WARN, "the provider's one connection is in use right now (watching or a restream); the recording needs it free at start time".into()));
        } else {
            let probe = eyes::probe_stream(cfg, s.stream_id, 6);
            if probe["skipped"] == true {
                out.push((WARN, format!("channel test skipped: {}", probe["reason"].as_str().unwrap_or(""))));
            } else if probe["ok"] != true {
                let errors: Vec<&str> = probe["errors"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
                let why = if errors.is_empty() { probe["reason"].as_str().unwrap_or("no video received").to_string() } else { errors.join("; ") };
                out.push((WARN, format!("could not pull video from {} right now ({why}); event channels often only go live shortly before the start", s.channel_name)));
            }
        }
    }

    let need = (s.duration_min as f64 * GB_PER_MINUTE * 10.0).round() / 10.0;
    let records = cfg.records_dir();
    let free = eyes::disk(if records.is_dir() { &records } else { &cfg.data_dir })["free_gb"].as_f64();
    if let Some(free) = free.filter(|f| *f < need) {
        out.push((FAIL, format!("only {free} GB free for the recording; it needs about {need} GB")));
    }
    if eyes::records_drive(cfg)["writable"] != true {
        out.push((WARN, "the recordings drive is not writable; the recording will go to the local folder instead".into()));
    }

    if s.show_name.is_some() {
        let jf = eyes::jellyfin_status(cfg);
        if jf["reachable"] != true {
            out.push((WARN, "Jellyfin is not answering; the episode will be saved but not scanned".into()));
        } else if jf["api_key_accepted"] == false {
            out.push((WARN, "Jellyfin rejected the API key; the episode will be saved but not scanned".into()));
        }
        match cfg.tv_library_root() {
            Some(library) if library.is_dir() && writable(&library) => {}
            library => out.push((FAIL, format!(
                "the TV library folder is not writable ({}); the episode cannot be placed",
                library.map(|l| l.display().to_string()).unwrap_or_else(|| "TVSHOW_LIBRARY_ROOT is not set".into())
            ))),
        }
    }

    if cfg.env("DISCORD_WEBHOOK_URL").is_none() {
        out.push((WARN, "no Discord webhook is configured, so results cannot be announced".into()));
    }
    out
}

fn writable(dir: &Path) -> bool {
    let probe = dir.join(".preflight_write_test");
    std::fs::write(&probe, b"x").and_then(|_| std::fs::remove_file(&probe)).is_ok()
}

/// Check the schedules that are due (or just `only`, right now). Returns a line per check.
pub fn run(cfg: &Config, only: Option<&str>) -> Result<Vec<String>> {
    let now = Local::now().naive_local();
    let state_file = cfg.data_dir.join("preflight_state.json");
    let mut state: Value = std::fs::read_to_string(&state_file).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_else(|| json!({}));
    for key in ["checked", "pinged"] {
        if !state[key].is_object() {
            state[key] = json!({});
        }
    }
    let stamp = |t: NaiveDateTime| t.format("%Y-%m-%dT%H:%M:%S").to_string();
    let mut report = vec![];
    for s in crate::schedules::list(&cfg.data_dir) {
        if !s.enabled || only.is_some_and(|id| id != s.id) {
            continue;
        }
        let Some(start) = s.next_run.as_deref().and_then(parse_next) else { continue };
        let minutes = (start - now).num_seconds() as f64 / 60.0;
        let key = format!("{}|{}", s.id, start.format("%Y-%m-%d %H:%M"));
        let due = (WINDOW_MIN..=WINDOW_MAX).contains(&minutes) && state["checked"].get(&key).is_none();
        if only.is_none() && !due {
            continue;
        }

        let results = checks_for(cfg, &s);
        let status = if results.iter().any(|r| r.0 == FAIL) { FAIL } else if results.is_empty() { OK } else { WARN };
        let entry = json!({ "at": stamp(now), "schedule_id": s.id, "channel": s.channel_name,
                            "starts_at": start.format("%Y-%m-%d %H:%M").to_string(), "status": status,
                            "problems": results.iter().map(|(st, m)| format!("{st}: {m}")).collect::<Vec<_>>() });
        let _ = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(cfg.data_dir.join("preflight.jsonl"))
            .and_then(|mut f| writeln!(f, "{entry}"));
        state["checked"][&key] = stamp(now).into();

        // Failures always ping; a warning repeats at most once a day.
        let mut lines = vec![];
        for (st, msg) in &results {
            let last = state["pinged"][msg.as_str()].as_str().and_then(|t| NaiveDateTime::parse_from_str(t, "%Y-%m-%dT%H:%M:%S").ok());
            let fresh = last.is_none_or(|t| (now - t).num_hours() >= REPING_HOURS);
            if *st == FAIL || fresh {
                lines.push(format!("{} {msg}", if *st == FAIL { "❌" } else { "⚠️" }));
                state["pinged"][msg.as_str()] = stamp(now).into();
            }
        }
        if !lines.is_empty() {
            let _ = crate::notify::discord(cfg, &format!("🛫 Pre-check for **{}** at {}:\n{}", s.channel_name, start.format("%H:%M"), lines.join("\n")));
        }
        report.push(format!("{} at {}: {status}", s.channel_name, start.format("%H:%M")));
        report.extend(results.iter().map(|(st, m)| format!("  {st}: {m}")));
    }

    let cutoff = now - chrono::Duration::days(3);
    if let Some(checked) = state["checked"].as_object_mut() {
        checked.retain(|_, v| v.as_str().and_then(|t| NaiveDateTime::parse_from_str(t, "%Y-%m-%dT%H:%M:%S").ok()).is_some_and(|t| t > cutoff));
    }
    std::fs::write(&state_file, state.to_string())?;
    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::parse_next;

    #[test]
    fn reads_systemd_next_elapse() {
        let t = parse_next("Thu 2026-10-22 18:29:00 EDT").unwrap();
        assert_eq!(t.format("%Y-%m-%d %H:%M").to_string(), "2026-10-22 18:29");
        assert!(parse_next("n/a").is_none());
    }
}
