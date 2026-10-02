#!/usr/bin/env python3
"""Run one recording schedule (data/schedules/<id>.json). Started by its
systemd user timer (see iptv_tui/domain/schedules.py).

Same steps as scripts/record_noovo_nightly.sh, but from data: a fresh
date-stamped output path, then record_scheduled.py, with the Jellyfin
post-processing when the schedule names a show. A one-time schedule removes
itself (and its timer) once it has run.
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from iptv_tui.domain import recordings, schedules  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: run_schedule.py <schedule-id>", file=sys.stderr)
        return 2
    s = schedules.load(sys.argv[1])
    if not s:
        print(f"no such schedule: {sys.argv[1]}", file=sys.stderr)
        return 1
    if not s.enabled:
        return 0

    records_path, warning = recordings.resolve_records_path()
    if warning:
        print(warning, file=sys.stderr)
    if not records_path:
        return 1
    output = recordings.generate_output_path(records_path, s.channel_name, datetime.now(), start_now=True)

    cmd = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "record_scheduled.py"),
        "--stream-id", str(s.stream_id),
        "--duration", str(s.duration_min * 60),
        "--output", output,
        "--channel-name", s.channel_name,
    ]
    if s.show_name:
        cmd += ["--postprocess-library-dir", str(Path(schedules.tv_library_root()) / s.show_name),
                "--postprocess-show-name", s.show_name]
    elif s.notify:
        cmd += ["--notify-discord"]

    code = subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
    if s.once:
        schedules.delete(s.id)
    return code


if __name__ == "__main__":
    sys.exit(main())
