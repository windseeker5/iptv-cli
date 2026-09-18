"""Scheduled recording database and systemd timer helpers."""

import os
import re
import sqlite3
import subprocess
import uuid
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from dateutil import parser as dateparser

from iptv_tui.domain import config, db


def list_recordings(limit: int = 50) -> list[dict]:
    """Return scheduled recordings ordered by start time descending."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, stream_id, channel_name, start_time, duration,
                   output_path, timer_unit, status, created_at
            FROM scheduled_recordings
            ORDER BY start_time DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def add_recording(
    stream_id: int,
    channel_name: str,
    start_time: datetime,
    duration_seconds: int,
    output_path: str,
    timer_unit: str,
) -> int | None:
    """Persist a scheduled recording to the database. Returns the new row id."""
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO scheduled_recordings
            (stream_id, channel_name, start_time, duration, output_path, timer_unit, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                stream_id,
                channel_name,
                int(start_time.timestamp()),
                duration_seconds,
                output_path,
                timer_unit,
                int(datetime.now().timestamp()),
            ),
        )
        conn.commit()
        return cursor.lastrowid


def update_recording_status(recording_id: int, status: str) -> None:
    """Update a scheduled recording status."""
    with db.connection() as conn:
        conn.execute(
            "UPDATE scheduled_recordings SET status = ? WHERE id = ?",
            (status, recording_id),
        )
        conn.commit()


def cancel_recording(recording_id: int) -> bool:
    """Mark a recording as cancelled in the database and stop its systemd timer."""
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timer_unit FROM scheduled_recordings WHERE id = ?", (recording_id,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            stop_timer(row[0])

        cursor.execute(
            "UPDATE scheduled_recordings SET status = 'cancelled' WHERE id = ?",
            (recording_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def stop_timer(timer_unit: str) -> dict:
    """Stop and disable a systemd user timer so it never fires."""
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", f"{timer_unit}.timer"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["systemctl", "--user", "stop", f"{timer_unit}.service"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["systemctl", "--user", "disable", f"{timer_unit}.timer"],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["systemctl", "--user", "reset-failed", f"{timer_unit}.timer"],
            capture_output=True, timeout=5,
        )
        return {"success": True, "message": "Timer stopped"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def delete_all_recordings() -> int:
    """Stop every recording's systemd timer and delete all rows. Returns rows deleted."""
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT timer_unit FROM scheduled_recordings")
        for (timer_unit,) in cursor.fetchall():
            if timer_unit:
                stop_timer(timer_unit)
        cursor.execute("DELETE FROM scheduled_recordings")
        conn.commit()
        return cursor.rowcount


_TIME_OF_DAY = re.compile(
    r"^(?P<hour>\d{1,2})(?:[:h.](?P<minute>\d{2}))?\s*(?P<meridiem>[ap]\.?m\.?)?$",
    re.IGNORECASE,
)
_BARE_DIGITS = re.compile(r"^(?P<digits>\d{3,4})\s*(?P<meridiem>[ap]\.?m\.?)?$", re.IGNORECASE)


def parse_time_of_day(text: str) -> tuple[int, int, bool] | None:
    """Parse a clock time.

    Accepts 18:29, 1829, 629, 6:29pm, 6pm, and 18. Returns
    (hour, minute, explicit_meridiem) or None when the text is not a clock time.
    """
    text = text.strip().lower().replace(" ", "")

    match = _BARE_DIGITS.match(text)
    if match:
        digits = match.group("digits")
        hour, minute = int(digits[:-2]), int(digits[-2:])
    else:
        match = _TIME_OF_DAY.match(text)
        if not match:
            return None
        hour = int(match.group("hour"))
        minute = int(match.group("minute") or 0)

    meridiem = (match.group("meridiem") or "").replace(".", "")
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if hour > 23 or minute > 59:
        return None
    return hour, minute, bool(meridiem)


def resolve_time_of_day(hour: int, minute: int, explicit_meridiem: bool, now: datetime) -> datetime:
    """Turn a clock time into the next datetime that matches it.

    A time that already passed today rolls to tomorrow, except that a bare
    12-hour time such as "630" prefers its PM reading later the same day, which
    is almost always what someone typing it at 18:20 means.
    """
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        return candidate

    if not explicit_meridiem and hour < 12:
        pm_candidate = candidate + timedelta(hours=12)
        if pm_candidate > now:
            return pm_candidate

    return candidate + timedelta(days=1)


def parse_start_time(start_input: str) -> datetime:
    """Parse user input into a datetime. Raises ValueError on failure."""
    start_input = start_input.strip()
    if not start_input:
        raise ValueError("Empty input")

    now = datetime.now()

    if start_input.lower() == "now":
        return now

    if start_input.lower().startswith("tomorrow"):
        time_part = start_input[len("tomorrow"):].strip()
        tomorrow = now.date() + timedelta(days=1)
        if not time_part:
            return datetime.combine(tomorrow, now.time())
        clock = parse_time_of_day(time_part)
        if not clock:
            raise ValueError(f"Could not read a time from: {time_part}")
        hour, minute, _ = clock
        return datetime.combine(tomorrow, dt_time(hour, minute))

    clock = parse_time_of_day(start_input)
    if clock:
        hour, minute, explicit_meridiem = clock
        return resolve_time_of_day(hour, minute, explicit_meridiem, now)

    parsed = dateparser.parse(start_input)
    if not parsed:
        raise ValueError(f"Could not parse date: {start_input}")
    if parsed < now:
        raise ValueError(
            f"{parsed.strftime('%Y-%m-%d %H:%M')} is in the past; use 'now' to start immediately"
        )
    return parsed


def sanitize_filename(name: str) -> str:
    """Make a safe filename base from a program/channel name."""
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    return safe.replace(" ", "_").lower()[:50]


def generate_output_path(
    records_path: str, base_name: str, start_time: datetime, start_now: bool = False
) -> str:
    """Generate a recording output file path."""
    date_str = (
        start_time.strftime("%Y-%m-%d_%H%M")
        if not start_now
        else datetime.now().strftime("%Y-%m-%d_%H%M")
    )
    filename = f"{sanitize_filename(base_name)}_{date_str}.ts"
    return os.path.join(records_path, filename)


FALLBACK_RECORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "recordings"


def _path_is_writable(path: str) -> str | None:
    """Return None when path exists and accepts writes, else the reason it does not."""
    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            return f"cannot create {path}: {e}"

    test_file = os.path.join(path, ".write_test")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        return f"cannot write to {path}: {e}"
    return None


def resolve_records_path(preferred_path: str | None = None) -> tuple[str | None, str | None]:
    """Return a writable records path plus an optional warning message.

    The path is None when no writable location could be found at all.

    When the configured path is unavailable — an unmounted USB drive is the
    usual cause — recordings fall back to data/recordings so scheduling still
    succeeds instead of failing outright.
    """
    path = preferred_path or config.Config.USB_RECORDS_PATH

    problem = _path_is_writable(path)
    if problem is None:
        return path, None

    fallback = str(FALLBACK_RECORDS_PATH)
    if os.path.abspath(fallback) == os.path.abspath(path):
        return None, f"Records path unusable: {problem}"

    fallback_problem = _path_is_writable(fallback)
    if fallback_problem is not None:
        return None, f"Records path unusable: {problem}; fallback {fallback_problem}"

    return fallback, f"{problem}; recording to {fallback} instead"


def build_systemd_command(
    stream_id: int,
    channel_name: str,
    duration_seconds: int,
    output_path: str,
    start_time: datetime,
    start_now: bool = False,
    recording_id: int | None = None,
    timer_unit: str | None = None,
) -> tuple[list[str], str]:
    """Build the systemd-run command and timer unit name."""
    timer_unit = timer_unit or f"iptv-record-{uuid.uuid4().hex[:12]}"
    wrapper_path = Path(__file__).resolve().parents[2] / "scripts" / "record_wrapper.sh"

    if start_now:
        trigger_arg = "--on-active=10s"
    else:
        local_tz = datetime.now().astimezone().strftime("%Z")
        calendar_str = f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} {local_tz}"
        trigger_arg = f"--on-calendar={calendar_str}"

    cmd = [
        "systemd-run",
        "--user",
        trigger_arg,
        f"--unit={timer_unit}",
        f"--property=RuntimeMaxSec={duration_seconds + 300}",
        "--description=IPTV Scheduled Recording",
        "/bin/bash",
        str(wrapper_path),
        "--stream-id",
        str(stream_id),
        "--duration",
        str(duration_seconds),
        "--output",
        output_path,
        "--channel-name",
        channel_name,
    ]
    if recording_id is not None:
        cmd.extend(["--recording-id", str(recording_id)])
    return cmd, timer_unit


def schedule_recording(
    stream_id: int,
    channel_name: str,
    start_time: datetime,
    duration_seconds: int,
    output_path: str | None = None,
) -> dict:
    """Schedule a recording via systemd-run and persist it to the database.

    Returns a dict with keys: success, recording_id, timer_unit, output_path, message.
    """
    if duration_seconds <= 0:
        return {"success": False, "message": "Duration must be greater than zero"}

    start_now = start_time <= datetime.now()
    records_path, warning = resolve_records_path()
    if records_path is None:
        return {"success": False, "message": warning or "No writable records path"}

    if not output_path:
        output_path = generate_output_path(records_path, channel_name, start_time, start_now)

    timer_unit = f"iptv-record-{uuid.uuid4().hex[:12]}"
    effective_start = start_time + timedelta(seconds=10) if start_now else start_time
    recording_id = add_recording(
        stream_id,
        channel_name,
        effective_start,
        duration_seconds,
        output_path,
        timer_unit,
    )
    if recording_id is None:
        return {"success": False, "message": "Could not save recording"}

    cmd, timer_unit = build_systemd_command(
        stream_id,
        channel_name,
        duration_seconds,
        output_path,
        start_time,
        start_now,
        recording_id=recording_id,
        timer_unit=timer_unit,
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            when = "in about 10 seconds" if start_now else effective_start.strftime("%a %Y-%m-%d %H:%M")
            message = f"Recording scheduled for {when} ({duration_seconds // 60} minutes)"
            if warning:
                message = f"{message} — {warning}"
            return {
                "success": True,
                "recording_id": recording_id,
                "timer_unit": timer_unit,
                "output_path": output_path,
                "warning": warning,
                "message": message,
            }

        update_recording_status(recording_id, "failed")
        return {
            "success": False,
            "message": f"Could not schedule recording: {result.stderr.strip()}",
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        update_recording_status(recording_id, "failed")
        return {"success": False, "message": "Timeout scheduling recording"}
    except FileNotFoundError:
        update_recording_status(recording_id, "failed")
        return {
            "success": False,
            "message": "systemd-run not found. This feature requires systemd (Linux)",
        }
    except Exception as e:
        update_recording_status(recording_id, "failed")
        return {"success": False, "message": f"Error scheduling recording: {e}"}
