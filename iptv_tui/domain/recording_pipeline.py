"""Post-recording pipeline: remux a .ts recording, place it in a Jellyfin
show library with season/episode naming, and trigger a Jellyfin rescan.

Each step is independent and logs its own failure rather than raising, since
by the time this runs the recording itself has already succeeded and should
not be reported as failed just because a later step could not complete.
"""

import logging
import re
import subprocess
from datetime import date
from pathlib import Path
import shutil

from iptv_tui.domain import config, jellyfin, remux

logger = logging.getLogger(__name__)

_PART_RE = re.compile(r"\.part(\d+)\.ts$")


def _concat_ts_parts(primary_path: str) -> str:
    """Merge provider-dropout retry segments into one .ts before remuxing.

    A recording that survives a mid-show provider dropout is split across
    <output>.ts plus <output>.part2.ts, <output>.part3.ts, ... (written by
    record_stream() in scripts/record_scheduled.py). Stream-copy concat them
    in attempt order so the whole show reaches Jellyfin, and remove the
    originals so nothing is left behind on the recording drive.
    """
    primary = Path(primary_path)
    parts = sorted(
        primary.parent.glob(f"{primary.stem}.part*.ts"),
        key=lambda p: int(_PART_RE.search(p.name).group(1)),
    )
    if not parts:
        return primary_path

    segments = [seg for seg in [primary, *parts] if seg.exists()]
    if len(segments) <= 1:
        return str(segments[0]) if segments else primary_path

    list_file = primary.with_name(f"{primary.stem}.concat_list.txt")
    concat_output = primary.with_name(f"{primary.stem}.concat.ts")
    list_file.write_text("".join(f"file '{seg.resolve()}'\n" for seg in segments))

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(concat_output),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        logger.error(f"Post-processing: concatenating recording parts failed: {exc}")
        list_file.unlink(missing_ok=True)
        return str(segments[0])
    list_file.unlink(missing_ok=True)

    if result.returncode != 0 or not concat_output.exists() or concat_output.stat().st_size == 0:
        last_error = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        logger.error(
            f"Post-processing: concatenating {len(segments)} recording parts "
            f"failed: {last_error or f'ffmpeg exit {result.returncode}'}"
        )
        concat_output.unlink(missing_ok=True)
        return str(segments[0])

    logger.info(
        f"Post-processing: merged {len(segments)} recording segments "
        f"(provider dropout) into {concat_output}"
    )
    for seg in segments:
        seg.unlink(missing_ok=True)
    return str(concat_output)


def _next_episode_path(show_dir: Path, show_name: str, air_date: date) -> Path:
    """Return the next SxxExx path for a nightly episode under show_dir.

    Season is the calendar year; episode number is the count of existing
    files in that season's folder, plus one.
    """
    season_dir = show_dir / f"Season {air_date.year}"
    existing = list(season_dir.glob("*.mp4")) if season_dir.exists() else []
    episode_num = len(existing) + 1
    filename = (
        f"{show_name} - S{air_date.year}E{episode_num:03d} - "
        f"{air_date.isoformat()}.mp4"
    )
    return season_dir / filename


def postprocess_recording(
    ts_path: str, show_dir: str | None = None, show_name: str = "Occupation Double"
) -> dict:
    """Remux, place into the Jellyfin library, and trigger a rescan.

    Returns a dict describing what happened at each step; never raises.
    """
    show_dir = show_dir or config.Config.NOOVO_LIBRARY_DIR
    result = {"remux": None, "placement": None, "jellyfin_scan": None}

    ts_path = _concat_ts_parts(ts_path)
    remux_result = remux.remux_for_tv(ts_path)
    result["remux"] = remux_result
    if not remux_result.get("success"):
        logger.error(f"Post-processing: remux failed: {remux_result.get('message')}")
        return result
    logger.info(f"Post-processing: remuxed to {remux_result['filepath']}")

    mp4_path = Path(remux_result["filepath"])
    try:
        target_dir = Path(show_dir)
        target = _next_episode_path(target_dir, show_name, date.today())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mp4_path), str(target))
        result["placement"] = {"success": True, "path": str(target)}
        logger.info(f"Post-processing: placed episode at {target}")
    except OSError as exc:
        result["placement"] = {"success": False, "message": str(exc)}
        logger.error(f"Post-processing: could not place file in library: {exc}")
        return result

    if not config.Config.JELLYFIN_USERNAME or not config.Config.JELLYFIN_PASSWORD:
        logger.warning(
            "Post-processing: JELLYFIN_USERNAME/JELLYFIN_PASSWORD not set, "
            "skipping library scan"
        )
        result["jellyfin_scan"] = {"success": False, "message": "credentials not configured"}
        return result

    token = jellyfin.authenticate(
        config.Config.JELLYFIN_PUBLISHED_SERVER_URL,
        config.Config.JELLYFIN_USERNAME,
        config.Config.JELLYFIN_PASSWORD,
    )
    if not token:
        result["jellyfin_scan"] = {"success": False, "message": "authentication failed"}
        return result

    scanned = jellyfin.trigger_library_scan(
        config.Config.JELLYFIN_PUBLISHED_SERVER_URL, token
    )
    result["jellyfin_scan"] = {"success": scanned}
    return result
