"""Scheduled recording database and systemd timer helpers."""

import os
import re
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from dateutil import parser as dateparser

from new_iptv.domain import config, db, iptv_provider


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


def cancel_recording(recording_id: int) -> bool:
    """Mark a recording as cancelled in the database."""
    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE scheduled_recordings SET status = 'cancelled' WHERE id = ?",
            (recording_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def check_timer_status(timer_unit: str) -> str:
    """Check whether a systemd user timer unit is active."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", f"{timer_unit}.timer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def parse_start_time(start_input: str) -> datetime:
    """Parse user input into a datetime. Raises ValueError on failure."""
    start_input = start_input.strip()
    if not start_input:
        raise ValueError("Empty input")

    now = datetime.now()

    if start_input.lower() == "now":
        return now

    if ":" in start_input and len(start_input) <= 5:
        parsed_time = datetime.strptime(start_input, "%H:%M").time()
        return datetime.combine(now.date(), parsed_time)

    if start_input.lower().startswith("tomorrow"):
        time_part = start_input.lower().replace("tomorrow", "").strip()
        tomorrow = now.date() + timedelta(days=1)
        if time_part:
            parsed_time = datetime.strptime(time_part, "%H:%M").time()
        else:
            parsed_time = now.time()
        return datetime.combine(tomorrow, parsed_time)

    parsed = dateparser.parse(start_input)
    if not parsed:
        raise ValueError(f"Could not parse date: {start_input}")
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


def resolve_records_path(preferred_path: str | None = None) -> tuple[str, str | None]:
    """Return the resolved records path and optional warning message."""
    path = preferred_path or config.Config.USB_RECORDS_PATH

    if not os.path.exists(path):
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            return path, f"Cannot create records path {path}: {e}"

    test_file = os.path.join(path, ".write_test")
    try:
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
    except Exception as e:
        return path, f"Cannot write to {path}: {e}"

    return path, None


def build_systemd_command(
    stream_id: int,
    channel_name: str,
    duration_seconds: int,
    output_path: str,
    start_time: datetime,
    start_now: bool = False,
) -> tuple[list[str], str]:
    """Build the systemd-run command and timer unit name."""
    timer_unit = f"iptv-record-{int(datetime.now().timestamp())}"
    wrapper_path = Path(__file__).resolve().parents[2] / "record_wrapper.sh"

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
    start_now = start_time <= datetime.now()
    records_path, warning = resolve_records_path()
    if warning:
        return {"success": False, "message": warning}

    if not output_path:
        output_path = generate_output_path(records_path, channel_name, start_time, start_now)

    cmd, timer_unit = build_systemd_command(
        stream_id,
        channel_name,
        duration_seconds,
        output_path,
        start_time,
        start_now,
    )

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            recording_id = add_recording(
                stream_id,
                channel_name,
                start_time + timedelta(seconds=10) if start_now else start_time,
                duration_seconds,
                output_path,
                timer_unit,
            )
            return {
                "success": True,
                "recording_id": recording_id,
                "timer_unit": timer_unit,
                "output_path": output_path,
                "message": "Recording scheduled successfully",
            }
        else:
            return {
                "success": False,
                "message": f"systemd-run failed: {result.stderr}",
                "stderr": result.stderr,
            }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Timeout scheduling recording"}
    except FileNotFoundError:
        return {
            "success": False,
            "message": "systemd-run not found. This feature requires systemd (Linux)",
        }
    except Exception as e:
        return {"success": False, "message": f"Error scheduling recording: {e}"}
