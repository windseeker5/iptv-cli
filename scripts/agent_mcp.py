#!/usr/bin/env python3
"""MCP server (stdio) exposing Pépère's toolbox to Claude Code.

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
from iptv_tui.domain import agent_actions as A  # noqa: E402

SESSION = os.environ.get("AGENT_SESSION", "")
USER = os.environ.get("AGENT_USER") or None
ROLE = os.environ.get("AGENT_ROLE", "family")
DAYS = {"type": "array", "items": {"type": "string", "enum": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]}}

TOOLS = {
    "find_channel": (
        "Search live TV channels by name (e.g. 'noovo', 'tva sports', 'rds'). Returns stream_id, full name, category.",
        {"query": {"type": "string"}}, ["query"],
        lambda a: A.find_channel(a["query"])),
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
    "propose_recording": (
        "Propose a recording. Repeating: give `days` (e.g. ['Mon','Tue','Wed','Thu']). One time: give `date` "
        "(YYYY-MM-DD) and no days. `time` is 24h local 'HH:MM'. Give `show_name` to convert to MP4 and put it "
        "in Jellyfin as that TV show (with a Discord ping when done). Nothing happens until the owner confirms.",
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
        "Propose restreaming a live channel to live.dresdell.com (replaces any current restream).",
        {"stream_id": {"type": "integer"}, "channel_name": {"type": "string"}}, ["stream_id", "channel_name"],
        lambda a: A.propose_start_restream(SESSION, a["stream_id"], a["channel_name"])),
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
