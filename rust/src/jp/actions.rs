//! JP's look-ups and proposals. Look-ups answer directly; actions (record, delete,
//! pause, restream, rebuild, rescan) only write a *pending* action in
//! `data/agent/pending/`. Nothing happens until someone taps Confirm on the OMT
//! page, which reaches `execute` through the runner. The model never executes
//! anything itself.

use crate::config::Config;
use crate::{db, notify, refresh, restream, runs, schedules, status};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// A proposal not confirmed within half an hour is dropped.
const PENDING_TTL_SECONDS: f64 = 30.0 * 60.0;
/// Family can do everything the owner can with recordings and the restream.
const FAMILY_ACTIONS: &[&str] = &["create_schedule", "delete_schedule", "pause_schedule", "start_restream", "stop_restream"];

fn agent_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("agent")
}

fn pending_dir(cfg: &Config) -> PathBuf {
    agent_dir(cfg).join("pending")
}

fn history_file(cfg: &Config) -> PathBuf {
    agent_dir(cfg).join("history.jsonl")
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn error(message: impl std::fmt::Display) -> Value {
    json!({ "error": message.to_string() })
}

// ── look-ups ─────────────────────────────────────────────────────────────

/// Matches plus how old the channel list is, so JP knows when to refresh first.
pub fn find_channel(cfg: &Config, query: &str) -> Value {
    let channels = match db::Db::open(&cfg.data_dir).and_then(|d| d.search_channels(query, 8)) {
        Ok(c) => c,
        Err(e) => return error(e),
    };
    let (list_age, guide_age) = status::list_age_minutes(&cfg.data_dir);
    json!({ "channels": channels, "list_age_minutes": list_age, "guide_age_minutes": guide_age })
}

/// Download the channel list now (about 5 s), plus the full guide if asked (about
/// 25 s more). A look-up, not an action: it changes no recording or restream.
pub fn refresh_channels(cfg: &Config, include_guide: bool) -> Value {
    let (list_age, _) = status::list_age_minutes(&cfg.data_dir);
    // A timer or another chat may have just done it.
    let result = if !include_guide && list_age.is_some_and(|a| a < 2) {
        Ok(format!("already fresh ({} min old)", list_age.unwrap_or(0)))
    } else {
        refresh::run(cfg, refresh::What { guide: include_guide, everything: false })
    };
    let (list_age, guide_age) = status::list_age_minutes(&cfg.data_dir);
    match result {
        Ok(message) => json!({ "ok": true, "message": message, "list_age_minutes": list_age, "guide_age_minutes": guide_age }),
        Err(e) => json!({ "ok": false, "message": format!("{e:#}"), "list_age_minutes": list_age, "guide_age_minutes": guide_age }),
    }
}

/// Now and the next three shows.
pub fn whats_on(cfg: &Config, stream_id: i64) -> Value {
    let programs = match db::Db::open(&cfg.data_dir).and_then(|d| d.guide(stream_id, chrono::Utc::now().timestamp(), 4)) {
        Ok(p) => p,
        Err(e) => return error(e),
    };
    if programs.is_empty() {
        return json!({ "programs": [], "note": "No guide information for this channel." });
    }
    json!({ "programs": programs })
}

pub fn list_schedules(cfg: &Config) -> Value {
    json!(schedules::list(&cfg.data_dir))
}

pub fn recent_recordings(cfg: &Config) -> Value {
    json!(runs::recent(&cfg.data_dir, 10))
}

pub fn restream_status(cfg: &Config) -> Value {
    match restream::active(&cfg.data_dir) {
        Some(a) => json!({ "live": true, "channel_name": a.channel_name, "since": a.started_at, "watch_at": cfg.watch_url }),
        None => json!({ "live": false }),
    }
}

// ── proposals (pending until confirmed) ──────────────────────────────────

pub struct Who<'a> {
    pub session: &'a str,
    pub user: Option<&'a str>,
}

fn propose(cfg: &Config, who: &Who, kind: &str, args: Value, summary: String) -> Value {
    let id = crate::jp::random_hex(6);
    let item = json!({ "id": id, "session": who.session, "kind": kind, "args": args, "summary": summary,
                       "user": who.user, "created": now() });
    let dir = pending_dir(cfg);
    if let Err(e) = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(dir.join(format!("{id}.json")), item.to_string())) {
        return error(format!("could not save the proposal: {e}"));
    }
    json!({ "pending_id": id, "summary": summary,
            "note": "Shown to the owner as a card with Confirm / Cancel. Nothing has happened yet." })
}

pub struct Recording<'a> {
    pub stream_id: i64,
    pub channel_name: &'a str,
    pub time: &'a str,
    pub duration_minutes: i64,
    pub days: Vec<String>,
    pub date: Option<String>,
    pub show_name: Option<&'a str>,
    pub notify: bool,
}

pub fn propose_recording(cfg: &Config, who: &Who, r: Recording) -> Value {
    let new = schedules::New {
        stream_id: r.stream_id,
        channel_name: r.channel_name,
        time: r.time,
        duration_min: r.duration_minutes,
        days: r.days.clone(),
        date: if r.days.is_empty() { r.date.clone() } else { None },
        show_name: r.show_name.filter(|s| !s.trim().is_empty()),
        notify: r.notify,
        created_by: who.user.unwrap_or("agent"),
    };
    let errors = schedules::validate(&new);
    if !errors.is_empty() {
        return error(errors.join("; "));
    }
    // Say it now rather than after the Confirm tap.
    if let Some(clash) = schedules::clash(&cfg.data_dir, &new) {
        return error(format!("this overlaps “{clash}”; the provider allows one connection, so only one recording can run at a time"));
    }
    let mut summary = format!("Record {}", schedules::describe_new(&new));
    if r.notify || new.show_name.is_some() {
        summary += " · Discord ping";
    }
    if let Some(u) = who.user {
        summary += &format!(" · for {u}");
    }
    let args = json!({ "stream_id": r.stream_id, "channel_name": r.channel_name, "time": r.time,
                       "duration_min": r.duration_minutes, "days": r.days, "date": new.date,
                       "show_name": new.show_name, "notify": r.notify, "for": who.user });
    propose(cfg, who, "create_schedule", args, summary)
}

pub fn propose_delete_schedule(cfg: &Config, who: &Who, id: &str) -> Value {
    match schedules::describe_id(&cfg.data_dir, id) {
        Some(what) => propose(cfg, &Who { user: None, ..*who }, "delete_schedule", json!({ "schedule_id": id }), format!("Delete: {what}")),
        None => error(format!("no schedule {id}")),
    }
}

pub fn propose_pause_schedule(cfg: &Config, who: &Who, id: &str, paused: bool) -> Value {
    match schedules::describe_id(&cfg.data_dir, id) {
        Some(what) => propose(
            cfg,
            &Who { user: None, ..*who },
            "pause_schedule",
            json!({ "schedule_id": id, "paused": paused }),
            format!("{}{what}", if paused { "Pause: " } else { "Resume: " }),
        ),
        None => error(format!("no schedule {id}")),
    }
}

/// Who started the restream that's on now (from the action log).
fn streaming_by(cfg: &Config) -> Option<String> {
    history(cfg, 200)
        .into_iter()
        .find(|h| (h["action"] == "start_restream" || h["action"] == "stop_restream") && h["ok"] == true)
        .filter(|h| h["action"] == "start_restream")
        .and_then(|h| h["user"].as_str().map(str::to_string))
}

pub fn propose_start_restream(cfg: &Config, who: &Who, stream_id: i64, channel_name: &str) -> Value {
    let live = restream::active(&cfg.data_dir);
    let by = live.as_ref().and_then(|_| streaming_by(cfg));
    let by_text = by.as_ref().map(|b| format!(" (started by {b})")).unwrap_or_default();
    let summary = match &live {
        Some(a) => format!("Replace {}{by_text} with {channel_name} on live.dresdell.com", a.channel_name),
        None => format!("Restream {channel_name} on live.dresdell.com"),
    };
    let mut out = propose(cfg, who, "start_restream", json!({ "stream_id": stream_id, "channel_name": channel_name }), summary);
    if let Some(a) = live {
        out["warning"] = format!(
            "Something is already live: {}{}. Tell the user it will be replaced if they confirm.",
            a.channel_name,
            by.map(|b| format!(", started by {b}")).unwrap_or_default()
        )
        .into();
    }
    out
}

pub fn propose_stop_restream(cfg: &Config, who: &Who) -> Value {
    match restream::active(&cfg.data_dir) {
        Some(a) => propose(cfg, &Who { user: None, ..*who }, "stop_restream", json!({}), format!("Stop restreaming {}", a.channel_name)),
        None => error("nothing is being restreamed right now"),
    }
}

/// Rebuild a library episode from the recording pieces still on disk.
pub fn propose_retry_run(cfg: &Config, who: &Who, run_id: &str) -> Value {
    let Some(d) = crate::jp::eyes::load_run(cfg, run_id) else {
        return error(format!("no recording report named {run_id}"));
    };
    let Some(show) = d["show_name"].as_str() else {
        return error("that recording was not meant for the Jellyfin library, so there is nothing to rebuild");
    };
    if let Some(placed) = d["result"]["episode_path"].as_str()
        && Path::new(placed).exists()
    {
        return error(format!("an episode was already placed ({}); nothing to rebuild", file_name(placed)));
    }
    let segments = crate::pipeline::find_segments(Path::new(d["output"].as_str().unwrap_or("")));
    if segments.is_empty() {
        return error("the recording pieces are no longer on disk, so it cannot be rebuilt");
    }
    let air: String = d["started_at"].as_str().unwrap_or("").chars().take(10).collect();
    if chrono::NaiveDate::parse_from_str(&air, "%Y-%m-%d").is_err() {
        return error("the report has no valid start date");
    }
    if let Some(existing) = episode_on(cfg, show, &air) {
        return error(format!("the library already has an episode for {air} ({existing}); say so and ask before adding a second one"));
    }
    let total: f64 = segments.iter().filter_map(|s| crate::pipeline::media_seconds(s)).sum();
    let summary = format!(
        "Rebuild “{show}” of {air} from {} saved piece(s) ({} of footage) and add it to Jellyfin",
        segments.len(),
        runs::format_seconds(Some(total))
    );
    propose(cfg, who, "retry_run", json!({ "run_id": run_id }), summary)
}

/// An episode of `show` already in the library for this air date (`… - 2026-10-21.mp4`).
fn episode_on(cfg: &Config, show: &str, air: &str) -> Option<String> {
    let show_dir = cfg.tv_library_root()?.join(show);
    let suffix = format!(" - {air}.mp4");
    std::fs::read_dir(show_dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|season| season.file_name().to_string_lossy().starts_with("Season "))
        .filter_map(|season| std::fs::read_dir(season.path()).ok())
        .flatten()
        .filter_map(Result::ok)
        .map(|f| f.file_name().to_string_lossy().to_string())
        .find(|name| name.ends_with(&suffix))
}

pub fn propose_rescan_jellyfin(cfg: &Config, who: &Who) -> Value {
    propose(cfg, who, "rescan_jellyfin", json!({}), "Ask Jellyfin to rescan its library now".into())
}

// ── confirm / cancel (called by the runner, never by the model) ──────────

fn read_pending(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// This chat's proposals still waiting for a tap, oldest first. Expired ones are dropped.
pub fn pending_for(cfg: &Config, session: &str) -> Vec<Value> {
    let Ok(entries) = std::fs::read_dir(pending_dir(cfg)) else { return vec![] };
    let mut out: Vec<Value> = vec![];
    for path in entries.filter_map(Result::ok).map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "json")) {
        let Some(item) = read_pending(&path) else { continue };
        if now() - item["created"].as_f64().unwrap_or(0.0) > PENDING_TTL_SECONDS {
            let _ = std::fs::remove_file(&path);
            continue;
        }
        if item["session"] == session {
            out.push(item);
        }
    }
    out.sort_by(|a, b| a["created"].as_f64().partial_cmp(&b["created"].as_f64()).unwrap_or(std::cmp::Ordering::Equal));
    out
}

fn log(cfg: &Config, mut entry: Value) {
    use std::io::Write;
    let at = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string();
    let mut line = json!({ "at": at });
    if let (Some(line), Some(entry)) = (line.as_object_mut(), entry.as_object_mut()) {
        line.append(entry);
    }
    let _ = std::fs::create_dir_all(agent_dir(cfg)).and_then(|_| {
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(history_file(cfg))?;
        writeln!(f, "{line}")
    });
}

fn pending_path(cfg: &Config, id: &str) -> Option<PathBuf> {
    (!id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric())).then(|| pending_dir(cfg).join(format!("{id}.json")))
}

pub fn cancel(cfg: &Config, id: &str, user: Option<&str>) -> bool {
    let Some(path) = pending_path(cfg, id) else { return false };
    let Some(item) = read_pending(&path) else { return false };
    let _ = std::fs::remove_file(&path);
    log(cfg, json!({ "action": "cancelled", "summary": item["summary"], "user": user }));
    true
}

pub fn execute(cfg: &Config, id: &str, user: Option<&str>, owner: bool) -> Value {
    let Some(path) = pending_path(cfg, id) else {
        return json!({ "ok": false, "message": "bad id" });
    };
    let Some(item) = read_pending(&path) else {
        return json!({ "ok": false, "message": "This action expired or was already handled." });
    };
    let kind = item["kind"].as_str().unwrap_or("").to_string();
    if !owner && !FAMILY_ACTIONS.contains(&kind.as_str()) {
        return json!({ "ok": false, "message": "Only the owner can do that one." });
    }
    let _ = std::fs::remove_file(&path);
    let a = &item["args"];
    let result = match run_action(cfg, &kind, a, user) {
        Ok(message) => json!({ "ok": true, "message": message }),
        Err(e) => json!({ "ok": false, "message": format!("{e:#}") }),
    };
    log(cfg, json!({ "action": kind, "summary": item["summary"], "ok": result["ok"], "message": result["message"], "user": user }));
    result
}

fn run_action(cfg: &Config, kind: &str, a: &Value, user: Option<&str>) -> anyhow::Result<String> {
    let text = |key: &str| a[key].as_str().unwrap_or("").to_string();
    match kind {
        "create_schedule" => {
            let days: Vec<String> = a["days"].as_array().map(|d| d.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
            let (channel, time, show) = (text("channel_name"), text("time"), a["show_name"].as_str().map(str::to_string));
            let s = schedules::create(
                cfg,
                schedules::New {
                    stream_id: a["stream_id"].as_i64().unwrap_or(0),
                    channel_name: &channel,
                    time: &time,
                    duration_min: a["duration_min"].as_i64().unwrap_or(0),
                    days,
                    date: a["date"].as_str().map(str::to_string),
                    show_name: show.as_deref(),
                    notify: a["notify"].as_bool().unwrap_or(true),
                    created_by: user.unwrap_or("agent"),
                },
            )?;
            Ok(format!("Scheduled: {}", s.what))
        }
        "delete_schedule" => {
            schedules::delete(cfg, &text("schedule_id"))?;
            Ok("Schedule deleted.".into())
        }
        "pause_schedule" => {
            let paused = a["paused"].as_bool().unwrap_or(true);
            let s = schedules::set_enabled(cfg, &text("schedule_id"), !paused)?;
            Ok(format!("{}{}", if paused { "Paused: " } else { "Resumed: " }, s.what))
        }
        "start_restream" => {
            let stream_id = a["stream_id"].as_i64().unwrap_or(0);
            let channel = db::Db::open(&cfg.data_dir)?
                .channel(stream_id)?
                .unwrap_or(db::Channel { stream_id, name: text("channel_name"), category: None });
            restream::start(cfg, &channel, true)?;
            Ok("Live on live.dresdell.com in a few seconds.".into())
        }
        "stop_restream" => {
            restream::stop(cfg)?;
            Ok("Restream stopped.".into())
        }
        "retry_run" => {
            start_retry(cfg, &text("run_id"))?;
            Ok("Started. It takes a minute or two, and Discord will ping when it's done.".into())
        }
        "rescan_jellyfin" => match notify::jellyfin_scan(cfg) {
            Ok(()) => Ok("Jellyfin is rescanning its library.".into()),
            Err(why) => anyhow::bail!("Could not rescan: {why}"),
        },
        other => anyhow::bail!("unknown action {other}"),
    }
}

/// The rebuild runs as its own systemd unit (it can take a minute or two).
fn start_retry(cfg: &Config, run_id: &str) -> anyhow::Result<()> {
    let exe = std::env::current_exe()?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.extend(["retry-run".to_string(), run_id.to_string()]);
    crate::systemd::run(crate::systemd::Run {
        unit: &format!("kdcli-retry-{}", chrono::Local::now().format("%Y%m%d-%H%M%S")),
        description: &format!("KDTV: rebuild {run_id} from its saved pieces"),
        working_dir: &cfg.cli_dir,
        max_seconds: Some(3600),
        command,
    })
}

/// The latest actions, newest first.
pub fn history(cfg: &Config, limit: usize) -> Vec<Value> {
    let text = std::fs::read_to_string(history_file(cfg)).unwrap_or_default();
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(limit)..].iter().rev().filter_map(|l| serde_json::from_str(l).ok()).collect()
}

fn file_name(path: &str) -> String {
    Path::new(path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
}
