"""Pépère's toolbox: the only things the OMT agent can do with KD CLI.

Two kinds of tools:
  - look-ups (find a channel, what's on, list schedules...) answer directly;
  - actions (schedule/delete a recording, start/stop a restream) only write a
    *pending* action. Nothing happens until the owner taps Confirm on the OMT
    page, which calls execute() through the agent runner. The model never
    executes anything itself.

Used by scripts/agent_mcp.py (the MCP server Claude talks to) and
scripts/agent_runner.py (confirm/cancel, page state).
"""
from __future__ import annotations

import json
import secrets
import time
from datetime import datetime
from pathlib import Path

from . import iptv_provider, recordings, restream, schedules

AGENT_DIR = Path(__file__).resolve().parents[2] / "data" / "agent"
PENDING_DIR = AGENT_DIR / "pending"
HISTORY_FILE = AGENT_DIR / "history.jsonl"
PENDING_TTL = 30 * 60
# Family can do everything the owner can (record, delete, pause, restream).
FAMILY_ACTIONS = {"create_schedule", "delete_schedule", "pause_schedule", "start_restream", "stop_restream"}
# The owner's extra eyes (read-only, see agent_eyes.py) and the fixes that need his Confirm.
OWNER_ONLY_TOOLS: set[str] = {
    "recent_runs", "run_report", "check_file", "probe_provider", "probe_stream", "system_health",
    "read_log", "search_source", "read_source", "propose_retry_run", "propose_rescan_jellyfin",
}


# ── look-ups ─────────────────────────────────────────────────────────────
def find_channel(query: str) -> dict:
    """Matches plus how old the channel list is, so JP knows when to refresh first."""
    from . import tv_refresh
    rows = iptv_provider.search_live_channels(query, limit=8)
    return {"channels": [{"stream_id": r["stream_id"], "name": r["name"], "category": r.get("category_name")}
                         for r in rows],
            "list_age_minutes": tv_refresh.age_minutes("channels"),
            "guide_age_minutes": tv_refresh.age_minutes("guide")}


def refresh_channels(include_guide: bool = False) -> dict:
    """Download the provider's current channel list now (about 5 s), plus the full guide
    if asked (about 25 s more). A look-up, not an action: it changes no recording or restream."""
    from . import tv_refresh
    r = tv_refresh.refresh(guide=include_guide, if_older_than_min=2)
    return {"ok": r["ok"], "channels": r.get("channels"), "guide": r.get("guide"),
            "list_age_minutes": r["channels_age_min"], "guide_age_minutes": r["guide_age_min"]}


def whats_on(stream_id: int, channel_name: str | None = None) -> dict:
    """Now + next 3, through the CLI's own (time-corrected) guide functions."""
    fmt = lambda ts: datetime.fromtimestamp(int(ts)).strftime("%a %H:%M")
    programs = []
    current = iptv_provider.get_now_playing(int(stream_id), channel_name=channel_name)
    when = time.time()
    for _ in range(4):
        p = current if not programs else iptv_provider.get_program_at(int(stream_id), when, channel_name=channel_name)
        if not p or not p.get("end"):
            break
        programs.append({"title": p.get("title"), "start": fmt(p["start"]), "end": fmt(p["end"]),
                         "description": (p.get("description") or "")[:200]})
        when = int(p["end"]) + 60
    return {"programs": programs} if programs else {"programs": [], "note": "No guide information for this channel."}


def list_schedules() -> list[dict]:
    out = []
    for s in schedules.list_schedules():
        out.append({"id": s.id, "what": s.describe(), "enabled": s.enabled, "next_run": schedules.next_run(s.id),
                    "channel_name": s.channel_name, "days": s.days, "date": s.date, "time": s.time,
                    "duration_min": s.duration_min, "show_name": s.show_name,
                    "created_by": getattr(s, "created_by", None)})
    return out


def recent_recordings(limit: int = 10) -> list[dict]:
    """Latest recordings from the run diary (what actually happened, including
    runs started by the systemd timers), falling back to the old table."""
    from . import run_report
    runs = run_report.recent(limit)
    if runs:
        out = []
        for r in runs:
            res = r.get("result") or {}
            out.append({"id": r["run_id"], "channel_name": r.get("channel_name"), "start_time": r.get("started_at"),
                        "planned": run_report.format_seconds(r.get("planned_seconds")),
                        "recorded": run_report.format_seconds(res.get("final_seconds") or res.get("captured_seconds")),
                        "status": r.get("status"), "problems": r.get("problems") or []})
        return out
    rows = recordings.list_recordings(limit=limit) or []
    keep = ("id", "channel_name", "start_time", "duration_seconds", "status", "output_path")
    return [{k: r.get(k) for k in keep if k in r} for r in rows]


def restream_status() -> dict:
    active = restream.get_active_restream()
    if not active:
        return {"live": False}
    return {"live": True, "channel_name": active.get("channel_name"), "since": active.get("started_at"),
            "watch_at": "https://live.dresdell.com/"}


# ── proposals (pending until confirmed) ──────────────────────────────────
def _propose(session: str, kind: str, args: dict, summary: str, user: str | None = None) -> dict:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    pid = secrets.token_hex(6)
    item = {"id": pid, "session": session, "kind": kind, "args": args, "summary": summary,
            "user": user, "created": time.time()}
    (PENDING_DIR / f"{pid}.json").write_text(json.dumps(item))
    return {"pending_id": pid, "summary": summary,
            "note": "Shown to the owner as a card with Confirm / Cancel. Nothing has happened yet."}


def propose_recording(session: str, stream_id: int, channel_name: str, time: str, duration_minutes: int,
                      days: list[str] | None = None, date: str | None = None,
                      show_name: str | None = None, notify: bool = True, user: str | None = None) -> dict:
    errors = schedules.validate(int(stream_id), channel_name, time, int(duration_minutes), days, date)
    if errors:
        return {"error": "; ".join(errors)}
    preview = schedules.Schedule(id="preview", stream_id=int(stream_id), channel_name=channel_name, time=time,
                                 duration_min=int(duration_minutes), days=days or [], date=None if days else date,
                                 show_name=show_name or None, notify=notify)
    summary = "Record " + preview.describe() + (" · Discord ping" if notify or show_name else "")
    return _propose(session, "create_schedule", {
        "stream_id": int(stream_id), "channel_name": channel_name, "time": time,
        "duration_min": int(duration_minutes), "days": days or [], "date": date,
        "show_name": show_name, "notify": notify, "for": user}, summary + (f" · for {user}" if user else ""), user)


def propose_delete_schedule(session: str, schedule_id: str) -> dict:
    s = schedules.load(schedule_id)
    if not s:
        return {"error": f"no schedule {schedule_id}"}
    return _propose(session, "delete_schedule", {"schedule_id": schedule_id}, "Delete: " + s.describe())


def propose_pause_schedule(session: str, schedule_id: str, paused: bool) -> dict:
    s = schedules.load(schedule_id)
    if not s:
        return {"error": f"no schedule {schedule_id}"}
    return _propose(session, "pause_schedule", {"schedule_id": schedule_id, "paused": bool(paused)},
                    ("Pause: " if paused else "Resume: ") + s.describe())


def propose_retry_run(session: str, run_id: str, user: str | None = None) -> dict:
    """Rebuild a library episode from the recording pieces still on disk."""
    from datetime import date as _date
    from . import recording_pipeline, run_report
    d = run_report.load(run_id) if run_id.replace("-", "").replace("_", "").replace(".", "").isalnum() else None
    if not d:
        return {"error": f"no recording report named {run_id}"}
    show = d.get("show_name")
    if not show:
        return {"error": "that recording was not meant for the Jellyfin library, so there is nothing to rebuild"}
    placed = (d.get("result") or {}).get("episode_path")
    if placed and Path(placed).exists():
        return {"error": f"an episode was already placed ({Path(placed).name}); nothing to rebuild"}
    segs = recording_pipeline.find_segments(d.get("output") or "")
    if not segs:
        return {"error": "the recording pieces are no longer on disk, so it cannot be rebuilt"}
    air = (d.get("started_at") or "")[:10]
    try:
        _date.fromisoformat(air)
    except ValueError:
        return {"error": "the report has no valid start date"}
    existing = list((Path(schedules.tv_library_root()) / show).glob(f"Season */* - {air}.mp4"))
    if existing:
        return {"error": f"the library already has an episode for {air} ({existing[0].name}); "
                         "say so and ask before adding a second one"}
    total = sum(recording_pipeline.media_seconds(s) or 0 for s in segs)
    summary = (f"Rebuild “{show}” of {air} from {len(segs)} saved piece(s) "
               f"({run_report.format_seconds(total)} of footage) and add it to Jellyfin")
    return _propose(session, "retry_run", {"run_id": run_id}, summary, user)


def propose_rescan_jellyfin(session: str, user: str | None = None) -> dict:
    return _propose(session, "rescan_jellyfin", {}, "Ask Jellyfin to rescan its library now", user)


def streaming_by() -> str | None:
    """Who started the restream that's on now (from the action log)."""
    for h in history(200):
        if h.get("action") in ("start_restream", "stop_restream") and h.get("ok"):
            return h.get("user") if h["action"] == "start_restream" else None
    return None


def propose_start_restream(session: str, stream_id: int, channel_name: str, user: str | None = None) -> dict:
    st = restream_status()
    summary = f"Restream {channel_name} on live.dresdell.com"
    if st.get("live"):
        by = streaming_by()
        summary = f"Replace {st.get('channel_name')}{f' (started by {by})' if by else ''} with {channel_name} on live.dresdell.com"
    out = _propose(session, "start_restream", {"stream_id": int(stream_id), "channel_name": channel_name}, summary, user)
    if st.get("live"):
        out["warning"] = (f"Something is already live: {st.get('channel_name')}"
                          + (f", started by {streaming_by()}" if streaming_by() else "")
                          + ". Tell the user it will be replaced if they confirm.")
    return out


def propose_stop_restream(session: str) -> dict:
    st = restream_status()
    if not st.get("live"):
        return {"error": "nothing is being restreamed right now"}
    return _propose(session, "stop_restream", {}, f"Stop restreaming {st.get('channel_name')}")


# ── confirm / cancel (called by the runner, never by the model) ──────────
def pending_for(session: str) -> list[dict]:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in PENDING_DIR.glob("*.json"):
        try:
            item = json.loads(f.read_text())
        except ValueError:
            continue
        if time.time() - item.get("created", 0) > PENDING_TTL:
            f.unlink(missing_ok=True)
            continue
        if item.get("session") == session:
            out.append(item)
    return sorted(out, key=lambda i: i["created"])


def _log(entry: dict) -> None:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def cancel(pending_id: str, user: str | None = None) -> bool:
    f = PENDING_DIR / f"{pending_id}.json"
    if not f.exists() or not pending_id.isalnum():
        return False
    item = json.loads(f.read_text())
    f.unlink(missing_ok=True)
    _log({"action": "cancelled", "summary": item.get("summary"), "user": user})
    return True


def execute(pending_id: str, user: str | None = None, role: str = "family") -> dict:
    if not pending_id.isalnum():
        return {"ok": False, "message": "bad id"}
    f = PENDING_DIR / f"{pending_id}.json"
    try:
        item = json.loads(f.read_text())
    except (OSError, ValueError):
        return {"ok": False, "message": "This action expired or was already handled."}
    kind, a = item["kind"], item["args"]
    if role != "owner" and kind not in FAMILY_ACTIONS:
        return {"ok": False, "message": "Only the owner can do that one."}
    f.unlink(missing_ok=True)
    try:
        if kind == "create_schedule":
            s = schedules.create(a["stream_id"], a["channel_name"], a["time"], a["duration_min"],
                                 days=a["days"], date=a["date"], show_name=a["show_name"], notify=a["notify"],
                                 created_by=user or "agent")
            result = {"ok": True, "message": f"Scheduled: {s.describe()}", "schedule_id": s.id,
                      "next_run": schedules.next_run(s.id)}
        elif kind == "delete_schedule":
            schedules.delete(a["schedule_id"])
            result = {"ok": True, "message": "Schedule deleted."}
        elif kind == "pause_schedule":
            s = schedules.set_enabled(a["schedule_id"], not a["paused"])
            result = {"ok": True, "message": ("Paused: " if a["paused"] else "Resumed: ") + s.describe()}
        elif kind == "start_restream":
            if restream.get_active_restream():
                restream.stop_restream()
            item_ = {"name": a["channel_name"], "stream_url": iptv_provider.build_stream_url(a["stream_id"])}
            r = restream.start_restream(item_)
            result = {"ok": bool(r.get("success")),
                      "message": "Live on live.dresdell.com in a few seconds." if r.get("success") else r.get("message", "Failed")}
        elif kind == "retry_run":
            result = _start_retry(a["run_id"])
        elif kind == "rescan_jellyfin":
            from . import recording_pipeline
            ok, why = recording_pipeline.scan_library()
            result = {"ok": ok, "message": "Jellyfin is rescanning its library." if ok else f"Could not rescan: {why}"}
        elif kind == "stop_restream":
            r = restream.stop_restream()
            result = {"ok": True, "message": "Restream stopped."}
        else:
            result = {"ok": False, "message": f"unknown action {kind}"}
    except Exception as exc:  # report, never crash the runner
        result = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    _log({"action": kind, "summary": item.get("summary"), "ok": result["ok"], "message": result["message"], "user": user})
    return result


def _start_retry(run_id: str) -> dict:
    """Run the rebuild as its own process (it can take a minute or two) and
    report on Discord when finished."""
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / f"retry_{run_id}.log", "ab") as log:
        subprocess.Popen([sys.executable, str(root / "scripts" / "retry_postprocess.py"), run_id],
                         cwd=root, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True, "message": "Started. It takes a minute or two, and Discord will ping when it's done."}


def history(limit: int = 20) -> list[dict]:
    try:
        lines = HISTORY_FILE.read_text().splitlines()[-limit:]
    except OSError:
        return []
    return [json.loads(l) for l in reversed(lines) if l.strip()]
