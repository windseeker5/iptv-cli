"""Recording schedules: once or repeating, as data + systemd user timers.

The NOOVO job (scripts/record_noovo_*.sh + systemd/iptv-noovo-record*.timer)
hardcodes its channel, days, time and duration in a shell script. This module
is the same pipeline driven by data, so Pépère (the OMT agent) and the TUI can
create, list and delete schedules:

  data/schedules/<id>.json            what to record (see Schedule)
  ~/.config/systemd/user/kdcli-rec-<id>.timer / .service
                                      when; the service runs
                                      scripts/run_schedule.py <id>

run_schedule.py builds a fresh date-stamped output path and calls
record_scheduled.py (ffmpeg with retries), with --postprocess-* when the
recording should go into Jellyfin (remux to MP4 -> show folder -> rescan ->
Discord ping), and --notify-discord otherwise.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULES_DIR = PROJECT_ROOT / "data" / "schedules"
UNIT_DIR = Path(os.environ.get("KDCLI_SYSTEMD_USER_DIR", Path.home() / ".config" / "systemd" / "user"))
UNIT_PREFIX = "kdcli-rec-"

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MAX_DURATION_MIN = 6 * 60


def tv_library_root() -> str:
    """Folder that holds one sub-folder per TV show in Jellyfin."""
    return os.getenv("TVSHOW_LIBRARY_ROOT") or str(Path(config.Config.NOOVO_LIBRARY_DIR).parent)


@dataclass
class Schedule:
    id: str
    stream_id: int
    channel_name: str
    time: str                      # "18:29", local time
    duration_min: int
    days: list[str] = field(default_factory=list)   # ["Mon", "Tue"...]; empty with `date` = once
    date: str | None = None        # "2026-10-02" for a one-time recording
    show_name: str | None = None   # set = remux + put into Jellyfin as this show
    notify: bool = True            # Discord message when done
    enabled: bool = True
    created_at: str = ""
    created_by: str = "agent"

    @property
    def once(self) -> bool:
        return not self.days

    @property
    def unit(self) -> str:
        return f"{UNIT_PREFIX}{self.id}"

    def on_calendar(self) -> str:
        if self.once:
            return f"{self.date} {self.time}:00"
        return f"{','.join(self.days)} *-*-* {self.time}:00"

    def describe(self) -> str:
        when = f"on {self.date}" if self.once else ("every " + "/".join(self.days))
        where = f" → Jellyfin “{self.show_name}”" if self.show_name else ""
        return f"{self.channel_name}, {when} at {self.time}, {self.duration_min} min{where}"


def validate(stream_id: int, channel_name: str, time: str, duration_min: int,
             days: list[str] | None, date: str | None) -> list[str]:
    errors = []
    if not isinstance(stream_id, int) or stream_id <= 0:
        errors.append("a channel (stream id) is required")
    if not channel_name:
        errors.append("a channel name is required")
    if not _TIME_RE.match(time or ""):
        errors.append("time must look like 18:29")
    if not (1 <= int(duration_min or 0) <= MAX_DURATION_MIN):
        errors.append(f"duration must be between 1 and {MAX_DURATION_MIN} minutes")
    days = days or []
    if any(d not in DAY_NAMES for d in days):
        errors.append("days must be among " + ", ".join(DAY_NAMES))
    if not days:
        if not date:
            errors.append("give either repeating days or a date")
        else:
            try:
                when = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                if when <= datetime.now():
                    errors.append("that date and time are already past")
            except ValueError:
                errors.append("date must look like 2026-10-02")
    return errors


def _path(schedule_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{4,40}", schedule_id):
        raise ValueError("bad schedule id")
    return SCHEDULES_DIR / f"{schedule_id}.json"


def load(schedule_id: str) -> Schedule | None:
    try:
        return Schedule(**json.loads(_path(schedule_id).read_text()))
    except (OSError, ValueError, TypeError):
        return None


def list_schedules() -> list[Schedule]:
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for f in sorted(SCHEDULES_DIR.glob("*.json")):
        s = load(f.stem)
        if s:
            out.append(s)
    return out


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def _write_units(s: Schedule) -> None:
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    runner = PROJECT_ROOT / "scripts" / "run_schedule.py"
    python = PROJECT_ROOT / "venv" / "bin" / "python3"
    (UNIT_DIR / f"{s.unit}.service").write_text(
        "[Unit]\n"
        f"Description=KD CLI recording: {s.describe()}\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={PROJECT_ROOT}\n"
        f"ExecStart={python} {runner} {s.id}\n"
    )
    (UNIT_DIR / f"{s.unit}.timer").write_text(
        "[Unit]\n"
        f"Description=When: {s.describe()}\n\n"
        "[Timer]\n"
        f"OnCalendar={s.on_calendar()}\n"
        f"Persistent={'false' if s.once else 'true'}\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def create(stream_id: int, channel_name: str, time: str, duration_min: int,
           days: list[str] | None = None, date: str | None = None,
           show_name: str | None = None, notify: bool = True, created_by: str = "agent") -> Schedule:
    errors = validate(stream_id, channel_name, time, duration_min, days, date)
    if errors:
        raise ValueError("; ".join(errors))
    days = [d for d in DAY_NAMES if d in (days or [])]   # canonical order
    s = Schedule(
        id=secrets.token_hex(4) + "-" + re.sub(r"[^a-z0-9]+", "-", channel_name.lower()).strip("-")[:20],
        stream_id=int(stream_id), channel_name=channel_name, time=time.zfill(5),
        duration_min=int(duration_min), days=days, date=None if days else date,
        show_name=(show_name or "").strip() or None, notify=notify,
        created_at=datetime.now().isoformat(timespec="seconds"), created_by=created_by,
    )
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    _path(s.id).write_text(json.dumps(asdict(s), indent=2))
    _write_units(s)
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", f"{s.unit}.timer")
    if r.returncode != 0:
        delete(s.id)
        raise RuntimeError(f"systemd refused the timer: {r.stderr.strip()[:200]}")
    return s


def set_enabled(schedule_id: str, enabled: bool) -> Schedule:
    s = load(schedule_id)
    if not s:
        raise KeyError(schedule_id)
    _systemctl("enable" if enabled else "disable", "--now", f"{s.unit}.timer")
    s.enabled = enabled
    _path(s.id).write_text(json.dumps(asdict(s), indent=2))
    return s


def delete(schedule_id: str) -> bool:
    s = load(schedule_id)
    unit = f"{UNIT_PREFIX}{schedule_id}"
    _systemctl("disable", "--now", f"{unit}.timer")
    for ext in ("timer", "service"):
        try:
            (UNIT_DIR / f"{unit}.{ext}").unlink()
        except FileNotFoundError:
            pass
    _systemctl("daemon-reload")
    try:
        _path(schedule_id).unlink()
    except FileNotFoundError:
        pass
    return s is not None


def next_run(schedule_id: str) -> str | None:
    """When systemd will next fire this schedule, as shown by systemctl."""
    r = _systemctl("show", f"{UNIT_PREFIX}{schedule_id}.timer", "--property=NextElapseUSecRealtime", "--value")
    value = (r.stdout or "").strip()
    return value or None
