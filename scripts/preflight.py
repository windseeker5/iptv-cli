#!/usr/bin/env python3
"""Pre-flight check before scheduled recordings.

Run every 10 minutes by kdcli-preflight.timer. For each enabled schedule that
starts 40-80 minutes from now (once per run), check what the recording needs:
provider login and free connection, a short test pull of the channel, disk
space, the backup drive, Jellyfin and the library folder, the Discord webhook.

Quiet when everything is fine; Discord only when something is wrong. Every
check is logged to data/preflight.jsonl, which JP reads (system_health).

    preflight.py            check schedules that are due
    preflight.py --now ID   check one schedule immediately (testing)
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from iptv_tui.domain import agent_eyes as E, config, notify, schedules  # noqa: E402

STATE = PROJECT_ROOT / "data" / "preflight_state.json"
WINDOW_MIN, WINDOW_MAX = 40, 80
GB_PER_MINUTE = 0.03          # ~3.7 Mbit/s stream, doubled for the .ts plus the converted copy
REPING_HOURS = 24             # the same warning is repeated at most once a day
OK, WARN, FAIL = "ok", "warning", "failed"


def next_start(schedule_id: str) -> datetime | None:
    value = schedules.next_run(schedule_id) or ""
    m = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value)
    return datetime.strptime(m.group(0), "%Y-%m-%d %H:%M:%S") if m else None


def checks_for(s: "schedules.Schedule") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    acct = E._provider_account()
    if not acct.get("reachable"):
        out.append((FAIL, f"the IPTV provider is not answering ({acct.get('error', 'no connection')})"))
    elif not acct.get("login_ok"):
        out.append((FAIL, f"the IPTV provider refused the login (HTTP {acct.get('http')})"))
    else:
        if acct.get("account_status") != "Active":
            out.append((FAIL, f"the IPTV account status is {acct.get('account_status')}"))
        days = acct.get("expires_in_days")
        if days is not None and days < 7:
            out.append((WARN if days >= 0 else FAIL, f"the IPTV subscription expires in {days} day(s)"))
        busy = acct["connections_allowed"] and acct["connections_in_use"] >= acct["connections_allowed"]
        if busy:
            out.append((WARN, "the provider's one connection is in use right now (watching or a restream); "
                              "the recording needs it free at start time"))
        else:
            probe = E.probe_stream(s.stream_id, 6)
            if probe.get("skipped"):
                out.append((WARN, f"channel test skipped: {probe.get('reason')}"))
            elif not probe.get("ok"):
                why = "; ".join(probe.get("errors") or []) or probe.get("reason") or "no video received"
                out.append((WARN, f"could not pull video from {s.channel_name} right now ({why}); "
                                  "event channels often only go live shortly before the start"))

    need = round(s.duration_min * GB_PER_MINUTE, 1)
    disk = E._disk(E.recordings_fallback())
    if disk.get("free_gb") is not None and disk["free_gb"] < need:
        out.append((FAIL, f"only {disk['free_gb']} GB free for the recording; it needs about {need} GB"))

    drive = E._backup_drive()
    if not drive["writable"]:
        out.append((WARN, "the backup drive is not writable; the recording will go to the local folder instead"))

    if s.show_name:
        jf = E._jellyfin_status()
        if not jf.get("reachable"):
            out.append((WARN, "Jellyfin is not answering; the episode will be saved but not scanned"))
        elif jf.get("api_key_accepted") is False:
            out.append((WARN, "Jellyfin rejected the API key; the episode will be saved but not scanned"))
        library = Path(schedules.tv_library_root())
        if not (library.is_dir() and os.access(library, os.W_OK)):
            out.append((FAIL, f"the TV library folder is not writable ({library}); the episode cannot be placed"))

    if not os.getenv("DISCORD_WEBHOOK_URL"):
        out.append((WARN, "no Discord webhook is configured, so results cannot be announced"))
    return out


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"checked": {}, "pinged": {}}


def main() -> int:
    now = datetime.now()
    state = load_state()
    forced = sys.argv[2] if len(sys.argv) == 3 and sys.argv[1] == "--now" else None
    due = []
    for s in schedules.list_schedules():
        if not s.enabled or (forced and s.id != forced):
            continue
        start = next_start(s.id)
        if not start:
            continue
        minutes = (start - now).total_seconds() / 60
        key = f"{s.id}|{start:%Y-%m-%d %H:%M}"
        if forced or (WINDOW_MIN <= minutes <= WINDOW_MAX and key not in state["checked"]):
            due.append((s, start, key))

    for s, start, key in due:
        results = checks_for(s)
        status = FAIL if any(r[0] == FAIL for r in results) else WARN if results else OK
        entry = {"at": now.isoformat(timespec="seconds"), "schedule_id": s.id, "channel": s.channel_name,
                 "starts_at": f"{start:%Y-%m-%d %H:%M}", "status": status,
                 "problems": [f"{st}: {msg}" for st, msg in results]}
        E.PREFLIGHT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(E.PREFLIGHT_LOG, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        state["checked"][key] = now.isoformat(timespec="seconds")

        # Failures always ping; a warning repeats at most once a day.
        lines = []
        for st, msg in results:
            last = state["pinged"].get(msg)
            fresh = not last or now - datetime.fromisoformat(last) > timedelta(hours=REPING_HOURS)
            if st == FAIL or fresh:
                lines.append(f"{'❌' if st == FAIL else '⚠️'} {msg}")
                state["pinged"][msg] = now.isoformat(timespec="seconds")
        if lines:
            notify.discord(f"🛫 Pre-check for **{s.channel_name}** at {start:%H:%M}:\n" + "\n".join(lines))
        print(f"{s.channel_name} at {start:%H:%M}: {status}", *[f"\n  {r[0]}: {r[1]}" for r in results])

    cutoff = now - timedelta(days=3)
    state["checked"] = {k: v for k, v in state["checked"].items() if datetime.fromisoformat(v) > cutoff}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
