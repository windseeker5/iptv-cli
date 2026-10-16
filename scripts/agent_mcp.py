#!/usr/bin/env python3
"""MCP server (stdio) exposing JP's toolbox to Claude Code.

Started by Claude Code itself, per chat turn, from the config the agent
runner writes (scripts/agent_runner.py). Minimal JSON-RPC over stdin/stdout,
no extra packages. The session id comes from the AGENT_SESSION env var so
proposed actions land in the right chat.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iptv_tui.domain import agent_actions as A, agent_eyes as E  # noqa: E402

SESSION = os.environ.get("AGENT_SESSION", "")
USER = os.environ.get("AGENT_USER") or None
ROLE = os.environ.get("AGENT_ROLE", "family")
DAYS = {"type": "array", "items": {"type": "string", "enum": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}}

TOOLS = {
    "find_channel": (
        "Search live TV channels by name (e.g. 'noovo', 'tva sports', 'rds'). Returns stream_id, full name, "
        "category, and list_age_minutes (how old the saved channel list is).",
        {"query": {"type": "string"}}, ["query"],
        lambda a: A.find_channel(a["query"])),
    "refresh_channels": (
        "Download the provider's current channel list right now (about 5 s). Set include_guide=true to also "
        "download the full TV guide (about 25 s more). Safe: it changes no recording or restream, no Confirm needed.",
        {"include_guide": {"type": "boolean"}}, [],
        lambda a: A.refresh_channels(bool(a.get("include_guide")))),
    "whats_on": (
        "What's on a channel now and next (guide), by stream_id.",
        {"stream_id": {"type": "integer"}, "channel_name": {"type": "string"}}, ["stream_id"],
        lambda a: A.whats_on(a["stream_id"], a.get("channel_name"))),
    "list_schedules": (
        "List the recording schedules (once or repeating) with their next run.",
        {}, [], lambda a: A.list_schedules()),
    "recent_recordings": (
        "List the most recent recordings and their status.",
        {}, [], lambda a: A.recent_recordings()),
    "restream_status": (
        "Is something being restreamed to live.dresdell.com right now?",
        {}, [], lambda a: A.restream_status()),
    # ── the owner's eyes (read-only; hidden from family, see A.OWNER_ONLY_TOOLS) ──
    "recent_runs": (
        "The latest recordings and how each one went (ok / warning / failed / running / crashed), with planned vs "
        "recorded length and the problems found. START HERE for 'did my recording work?' or 'what went wrong?'.",
        {"limit": {"type": "integer"}}, [],
        lambda a: E.recent_runs(a.get("limit", 8))),
    "run_report": (
        "The step-by-step diary of one recording (by run_id from recent_runs): every attempt and provider dropout "
        "with clock times, convert, join, length check, placement, Jellyfin scan, Discord, and the pieces still on "
        "disk. This is the explanation of what happened.",
        {"run_id": {"type": "string"}}, ["run_id"],
        lambda a: E.run_report_for(a["run_id"])),
    "check_file": (
        "Does a recording or library episode look good? Pass a run_id or a file path. Reports real length vs "
        "planned, video/audio streams, and samples a few stretches for black picture, frozen picture, silence and "
        "decode errors. Takes up to ~30 s. Black/frozen can just be a title card or ad break; say so.",
        {"target": {"type": "string"}, "samples": {"type": "integer"}}, ["target"],
        lambda a: E.check_file(a["target"], a.get("samples", 5))),
    "probe_provider": (
        "Is the IPTV provider up? Login accepted, account status, days until it expires, connections in use vs "
        "allowed, channel list age. The plan allows ONE connection.",
        {}, [], lambda a: E.probe_provider()),
    "probe_stream": (
        "Test a live channel by pulling ~8 s of it: is the feed healthy (bitrate, resolution, errors)? Needs the "
        "provider's one connection, so it refuses while a recording, restream or viewer is using it.",
        {"stream_id": {"type": "integer"}, "seconds": {"type": "integer"}}, ["stream_id"],
        lambda a: E.probe_stream(a["stream_id"], a.get("seconds", 8))),
    "system_health": (
        "One look at everything a recording depends on: provider, Jellyfin (and whether the API key works), "
        "backup drive, disk space, restream, each schedule's next run and last result, leftover recording pieces "
        "that never reached the library, and the last pre-flight check.",
        {}, [], lambda a: E.system_health()),
    "read_log": (
        "Read a KD CLI unit's journal (recordings kdcli-rec-*.service, refresh jobs, kdcli-agent-runner.service). "
        "Call without `unit` to list them. `since` like '2026-10-11 18:00' or '6 hours ago'; `grep` filters lines. "
        "Prefer run_report first; use this when the diary is missing or you need raw detail.",
        {"unit": {"type": "string"}, "since": {"type": "string"}, "grep": {"type": "string"},
         "lines": {"type": "integer"}}, [],
        lambda a: E.read_log(a.get("unit"), a.get("since", "24 hours ago"), a.get("grep"), a.get("lines", 80))),
    "search_source": (
        "Find where something is in the code of KD CLI (kdtv/cli) and the OMT site. Substring search, 3+ chars.",
        {"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"],
        lambda a: E.search_source(a["query"], a.get("limit", 25))),
    "read_source": (
        "Read part of a code file (from search_source results). Read-only; never .env or data folders. You can "
        "explain the code and describe a fix, but you cannot edit it.",
        {"path": {"type": "string"}, "start": {"type": "integer"}, "lines": {"type": "integer"}}, ["path"],
        lambda a: E.read_source(a["path"], a.get("start", 1), a.get("lines", 120))),
    "propose_retry_run": (
        "Propose rebuilding a library episode from the recording pieces still on disk (use when a run failed at "
        "convert/join/place, or a long show only has a stub). Fails if an episode for that date is already in the "
        "library. Nothing happens until the owner confirms; Discord pings when it finishes.",
        {"run_id": {"type": "string"}}, ["run_id"],
        lambda a: A.propose_retry_run(SESSION, a["run_id"], USER)),
    "propose_rescan_jellyfin": (
        "Propose asking Jellyfin to rescan its library (use when an episode is in the folder but not showing). "
        "Nothing happens until the owner confirms.",
        {}, [], lambda a: A.propose_rescan_jellyfin(SESSION, USER)),
    "propose_recording": (
        "Propose a recording. Repeating: give `days` (e.g. ['Mon','Tue','Wed','Thu']). One time: give `date` "
        "(YYYY-MM-DD) and no days. `time` is 24h local 'HH:MM'. Give `show_name` to convert to MP4 and put it "
        "in Jellyfin as that TV show (with a Discord ping when done). Nothing happens until the user confirms.",
        {"stream_id": {"type": "integer"}, "channel_name": {"type": "string"}, "time": {"type": "string"},
         "duration_minutes": {"type": "integer"}, "days": DAYS, "date": {"type": "string"},
         "show_name": {"type": "string"}, "notify": {"type": "boolean"}},
        ["stream_id", "channel_name", "time", "duration_minutes"],
        lambda a: A.propose_recording(SESSION, a["stream_id"], a["channel_name"], a["time"], a["duration_minutes"],
                                      a.get("days"), a.get("date"), a.get("show_name"), a.get("notify", True), USER)),
    "propose_delete_schedule": (
        "Propose deleting a recording schedule (by id from list_schedules).",
        {"schedule_id": {"type": "string"}}, ["schedule_id"],
        lambda a: A.propose_delete_schedule(SESSION, a["schedule_id"])),
    "propose_pause_schedule": (
        "Propose pausing (paused=true) or resuming (paused=false) a schedule.",
        {"schedule_id": {"type": "string"}, "paused": {"type": "boolean"}}, ["schedule_id", "paused"],
        lambda a: A.propose_pause_schedule(SESSION, a["schedule_id"], a["paused"])),
    "propose_start_restream": (
        "Propose restreaming a live channel to live.dresdell.com. If something is already live, the result has a "
        "`warning`: tell the user who is streaming what, and that confirming replaces it.",
        {"stream_id": {"type": "integer"}, "channel_name": {"type": "string"}}, ["stream_id", "channel_name"],
        lambda a: A.propose_start_restream(SESSION, a["stream_id"], a["channel_name"], USER)),
    "propose_stop_restream": (
        "Propose stopping the current restream.",
        {}, [], lambda a: A.propose_stop_restream(SESSION)),
}


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    out["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:          # notifications (e.g. notifications/initialized)
            continue
        if method == "initialize":
            reply(msg_id, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "kdtv", "version": "1.0"}})
        elif method == "ping":
            reply(msg_id, {})
        elif method == "tools/list":
            reply(msg_id, {"tools": [
                {"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r}}
                for n, (d, p, r, _) in TOOLS.items() if ROLE == "owner" or n not in A.OWNER_ONLY_TOOLS]})
        elif method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            tool = TOOLS.get(name) if (ROLE == "owner" or name not in A.OWNER_ONLY_TOOLS) else None
            if not tool:
                reply(msg_id, error={"code": -32602, "message": "unknown tool"})
                continue
            try:
                data = tool[3](params.get("arguments") or {})
                is_error = isinstance(data, dict) and "error" in data
                reply(msg_id, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
                               "isError": is_error})
            except Exception as exc:
                reply(msg_id, {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True})
        else:
            reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
