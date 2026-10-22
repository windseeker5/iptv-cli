//! `kdtv jp-mcp`: the MCP server (JSON-RPC over stdin/stdout) exposing JP's
//! toolbox to Claude Code. Claude Code starts it per chat turn from the config
//! the runner writes; AGENT_SESSION, AGENT_USER and AGENT_ROLE say which chat
//! proposals belong to and which tools this person gets.

use super::actions::{self as a, Who};
use super::eyes as e;
use crate::config::Config;
use serde_json::{json, Value};
use std::io::{BufRead, Write};

/// The owner's eyes (read-only) and the fixes that need his Confirm.
pub const OWNER_ONLY: &[&str] = &[
    "recent_runs", "run_report", "check_file", "probe_provider", "probe_stream", "system_health", "read_log", "search_source",
    "read_source", "propose_retry_run", "propose_rescan_jellyfin",
];

struct Tool {
    name: &'static str,
    description: &'static str,
    properties: Value,
    required: &'static [&'static str],
}

fn tools() -> Vec<Tool> {
    let days = json!({ "type": "array", "items": { "type": "string", "enum": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] } });
    let t = |name, description, properties: Value, required| Tool { name, description, properties, required };
    vec![
        t("find_channel",
          "Search live TV channels by name (e.g. 'noovo', 'tva sports', 'rds'). Returns stream_id, full name, category, \
           list_age_minutes and guide_age_minutes (how old the saved channel list and guide are; background jobs refresh \
           the list every hour and the guide twice a day).",
          json!({ "query": { "type": "string" } }), &["query"]),
        t("refresh_channels",
          "Download the provider's current channel list right now (about 5 s). Rarely needed: it is refreshed every hour \
           in the background. Set include_guide=true to also download the full TV guide (about 25 s more; it is refreshed \
           twice a day in the background). Safe: it changes no recording or restream, no Confirm needed.",
          json!({ "include_guide": { "type": "boolean" } }), &[]),
        t("whats_on", "What's on a channel now and next (guide), by stream_id.",
          json!({ "stream_id": { "type": "integer" }, "channel_name": { "type": "string" } }), &["stream_id"]),
        t("maxime_games",
          "Maxime's hockey games (UMass Minutemen): the next games with local time, home/away, opponent, TV network, and \
           for games in the next 3 days the channels WE have for it (ESPN+ event channels, guide matches) with stream_id; \
           plus the latest results and the team record. Official schedule, refreshed every morning. Use it for ANY \
           question about Maxime/Max/UMass games, before find_channel.",
          json!({ "upcoming": { "type": "integer" }, "past": { "type": "integer" } }), &[]),
        t("list_schedules", "List the recording schedules (once or repeating) with their next run.", json!({}), &[]),
        t("recent_recordings", "List the most recent recordings and their status.", json!({}), &[]),
        t("restream_status", "Is something being restreamed to live.dresdell.com right now?", json!({}), &[]),
        t("recent_runs",
          "The latest recordings and how each one went (ok / warning / failed / running / stopped / crashed), with \
           planned vs recorded length and the problems found. START HERE for 'did my recording work?' or 'what went wrong?'. \
           Each run's `id` is the run_id the other tools take.",
          json!({ "limit": { "type": "integer" } }), &[]),
        t("run_report",
          "The step-by-step diary of one recording (by run_id = `id` from recent_runs): every attempt and provider dropout \
           with clock times, convert, join, length check, placement, Jellyfin scan, Discord, and the pieces still on disk. \
           This is the explanation of what happened.",
          json!({ "run_id": { "type": "string" } }), &["run_id"]),
        t("check_file",
          "Does a recording or library episode look good? Pass a run_id or a file path. Reports real length vs planned, \
           video/audio streams, and samples a few stretches for black picture, frozen picture, silence and decode errors. \
           Takes up to ~30 s. Black/frozen can just be a title card or ad break; say so.",
          json!({ "target": { "type": "string" }, "samples": { "type": "integer" } }), &["target"]),
        t("probe_provider",
          "Is the IPTV provider up? Login accepted, account status, days until it expires, connections in use vs allowed, \
           channel list age. The plan allows ONE connection.",
          json!({}), &[]),
        t("probe_stream",
          "Test a live channel by pulling ~8 s of it: is the feed healthy (bitrate, resolution, errors)? Needs the \
           provider's one connection, so it refuses while a recording, restream or viewer is using it.",
          json!({ "stream_id": { "type": "integer" }, "seconds": { "type": "integer" } }), &["stream_id"]),
        t("system_health",
          "One look at everything a recording depends on: provider, Jellyfin (and whether the API key works), recordings \
           drive, disk space, restream, each schedule's next run and last result, leftover recording pieces that never \
           reached the library, and the last pre-flight check.",
          json!({}), &[]),
        t("read_log",
          "Read a KDTV unit's journal (recordings kdcli-rec-*.service, refresh jobs, kdcli-agent-runner.service). Call \
           without `unit` to list them. `since` like '2026-10-11 18:00' or '6 hours ago'; `grep` filters lines. Prefer \
           run_report first; use this when the diary is missing or you need raw detail.",
          json!({ "unit": { "type": "string" }, "since": { "type": "string" }, "grep": { "type": "string" }, "lines": { "type": "integer" } }), &[]),
        t("search_source", "Find where something is in the code of KDTV (kdtv/cli, the Rust program kdtv) and the OMT site. Substring search, 3+ chars.",
          json!({ "query": { "type": "string" }, "limit": { "type": "integer" } }), &["query"]),
        t("read_source",
          "Read part of a code file (from search_source results). Read-only; never .env or data folders. You can explain \
           the code and describe a fix, but you cannot edit it.",
          json!({ "path": { "type": "string" }, "start": { "type": "integer" }, "lines": { "type": "integer" } }), &["path"]),
        t("propose_retry_run",
          "Propose rebuilding a library episode from the recording pieces still on disk (use when a run failed at \
           convert/join/place, or a long show only has a stub). Fails if an episode for that date is already in the \
           library. Nothing happens until the owner confirms; Discord pings when it finishes.",
          json!({ "run_id": { "type": "string" } }), &["run_id"]),
        t("propose_rescan_jellyfin",
          "Propose asking Jellyfin to rescan its library (use when an episode is in the folder but not showing). Nothing \
           happens until the owner confirms.",
          json!({}), &[]),
        t("propose_recording",
          "Propose a recording. Repeating: give `days` (e.g. ['Mon','Tue','Wed','Thu']). One time: give `date` \
           (YYYY-MM-DD) and no days. `time` is 24h local 'HH:MM'. Give `show_name` to convert to MP4 and put it in \
           Jellyfin as that TV show (with a Discord ping when done). Refused when it overlaps another recording (one \
           provider connection). Nothing happens until the user confirms.",
          json!({ "stream_id": { "type": "integer" }, "channel_name": { "type": "string" }, "time": { "type": "string" },
                  "duration_minutes": { "type": "integer" }, "days": days, "date": { "type": "string" },
                  "show_name": { "type": "string" }, "notify": { "type": "boolean" } }),
          &["stream_id", "channel_name", "time", "duration_minutes"]),
        t("propose_delete_schedule", "Propose deleting a recording schedule (by id from list_schedules).",
          json!({ "schedule_id": { "type": "string" } }), &["schedule_id"]),
        t("propose_pause_schedule", "Propose pausing (paused=true) or resuming (paused=false) a schedule.",
          json!({ "schedule_id": { "type": "string" }, "paused": { "type": "boolean" } }), &["schedule_id", "paused"]),
        t("propose_start_restream",
          "Propose restreaming a live channel to live.dresdell.com. If something is already live, the result has a \
           `warning`: tell the user who is streaming what, and that confirming replaces it.",
          json!({ "stream_id": { "type": "integer" }, "channel_name": { "type": "string" } }), &["stream_id", "channel_name"]),
        t("propose_stop_restream", "Propose stopping the current restream.", json!({}), &[]),
    ]
}

fn call(cfg: &Config, who: &Who, name: &str, args: &Value) -> Value {
    let s = |k: &str| args[k].as_str().unwrap_or("");
    let n = |k: &str, default: i64| args[k].as_i64().unwrap_or(default);
    let b = |k: &str| args[k].as_bool();
    match name {
        "find_channel" => a::find_channel(cfg, s("query")),
        "refresh_channels" => a::refresh_channels(cfg, b("include_guide").unwrap_or(false)),
        "whats_on" => a::whats_on(cfg, n("stream_id", 0)),
        "maxime_games" => crate::maxime::games(cfg, n("upcoming", 3).max(0) as usize, n("past", 1).max(0) as usize),
        "list_schedules" => a::list_schedules(cfg),
        "recent_recordings" => a::recent_recordings(cfg),
        "restream_status" => a::restream_status(cfg),
        "recent_runs" => e::recent_runs(cfg, n("limit", 8).max(1) as usize),
        "run_report" => e::run_report(cfg, s("run_id")),
        "check_file" => e::check_file(cfg, s("target"), n("samples", 5).max(0) as usize),
        "probe_provider" => e::probe_provider(cfg),
        "probe_stream" => e::probe_stream(cfg, n("stream_id", 0), n("seconds", 8)),
        "system_health" => e::system_health(cfg),
        "read_log" => {
            let unit = args["unit"].as_str();
            let since = args["since"].as_str().unwrap_or("24 hours ago");
            e::read_log(cfg, unit, since, args["grep"].as_str(), n("lines", 80).max(0) as usize)
        }
        "search_source" => e::search_source(cfg, s("query"), n("limit", 25).max(1) as usize),
        "read_source" => e::read_source(cfg, s("path"), n("start", 1).max(1) as usize, n("lines", 120).max(0) as usize),
        "propose_retry_run" => a::propose_retry_run(cfg, who, s("run_id")),
        "propose_rescan_jellyfin" => a::propose_rescan_jellyfin(cfg, who),
        "propose_recording" => {
            let days = args["days"].as_array().map(|d| d.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
            a::propose_recording(
                cfg,
                who,
                a::Recording {
                    stream_id: n("stream_id", 0),
                    channel_name: s("channel_name"),
                    time: s("time"),
                    duration_minutes: n("duration_minutes", 0),
                    days,
                    date: args["date"].as_str().map(str::to_string),
                    show_name: args["show_name"].as_str(),
                    notify: b("notify").unwrap_or(true),
                },
            )
        }
        "propose_delete_schedule" => a::propose_delete_schedule(cfg, who, s("schedule_id")),
        "propose_pause_schedule" => a::propose_pause_schedule(cfg, who, s("schedule_id"), b("paused").unwrap_or(true)),
        "propose_start_restream" => a::propose_start_restream(cfg, who, n("stream_id", 0), s("channel_name")),
        "propose_stop_restream" => a::propose_stop_restream(cfg, who),
        _ => json!({ "error": "unknown tool" }),
    }
}

/// Serve MCP on stdin/stdout until Claude Code closes it.
pub fn serve(cfg: &Config) -> anyhow::Result<()> {
    let session = std::env::var("AGENT_SESSION").unwrap_or_default();
    let user = std::env::var("AGENT_USER").ok().filter(|u| !u.is_empty());
    let owner = std::env::var("AGENT_ROLE").is_ok_and(|r| r == "owner");
    let who = Who { session: &session, user: user.as_deref() };
    let allowed = |name: &str| owner || !OWNER_ONLY.contains(&name);
    let tools = tools();

    let stdin = std::io::stdin();
    let mut out = std::io::stdout();
    for line in stdin.lock().lines() {
        let Ok(msg) = serde_json::from_str::<Value>(line?.trim()) else { continue };
        let id = msg["id"].clone();
        if id.is_null() {
            continue; // notifications (e.g. notifications/initialized)
        }
        let reply = match msg["method"].as_str().unwrap_or("") {
            "initialize" => Ok(json!({
                "protocolVersion": msg["params"]["protocolVersion"].as_str().unwrap_or("2025-06-18"),
                "capabilities": { "tools": {} },
                "serverInfo": { "name": "kdtv", "version": env!("CARGO_PKG_VERSION") },
            })),
            "ping" => Ok(json!({})),
            "tools/list" => Ok(json!({ "tools": tools.iter().filter(|t| allowed(t.name)).map(|t| json!({
                "name": t.name, "description": t.description,
                "inputSchema": { "type": "object", "properties": t.properties, "required": t.required },
            })).collect::<Vec<_>>() })),
            "tools/call" => {
                let name = msg["params"]["name"].as_str().unwrap_or("");
                if tools.iter().any(|t| t.name == name) && allowed(name) {
                    let args = msg["params"]["arguments"].clone();
                    let data = call(cfg, &who, name, if args.is_object() { &args } else { &Value::Null });
                    let is_error = data.get("error").is_some();
                    Ok(json!({ "content": [{ "type": "text", "text": data.to_string() }], "isError": is_error }))
                } else {
                    Err(json!({ "code": -32602, "message": "unknown tool" }))
                }
            }
            other => Err(json!({ "code": -32601, "message": format!("method not found: {other}") })),
        };
        let mut response = json!({ "jsonrpc": "2.0", "id": id });
        match reply {
            Ok(result) => response["result"] = result,
            Err(error) => response["error"] = error,
        }
        writeln!(out, "{response}")?;
        out.flush()?;
    }
    Ok(())
}
