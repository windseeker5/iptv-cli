"""Post-recording pipeline: convert a .ts recording to MP4, place it in a
Jellyfin show library with season/episode naming, and trigger a Jellyfin
rescan.

Each step is independent and reports its own failure rather than raising,
since by the time this runs the recording itself has already succeeded and
should not be reported as failed just because a later step could not
complete. Every step is also written to the run report (run_report.py) so
the story of a recording can be read back later.

Originals are kept until the episode is safely in the library. A failure
never ships a partial file as if it were the whole show.
"""

import logging
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from iptv_tui.domain import config, jellyfin, remux
from iptv_tui.domain.run_report import FAILED, OK, WARNING, format_seconds

logger = logging.getLogger(__name__)

_PART_RE = re.compile(r"\.part(\d+)\.ts$")

# Share of the planned length that counts as a complete recording.
LENGTH_OK = 0.95
LENGTH_FAILED = 0.70


def _step(report, step: str, status: str, message: str, **data) -> None:
    """Log a pipeline step and add it to the run report."""
    log = {OK: logger.info, WARNING: logger.warning, FAILED: logger.error}[status]
    log(f"Post-processing [{step}]: {message}")
    if report is not None:
        report.event(step, status, message, **data)


def media_seconds(path: str | Path) -> float | None:
    """Real duration of a media file via ffprobe, or None when unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return float(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def find_segments(primary_path: str) -> list[Path]:
    """The recording's pieces in attempt order.

    A recording that survives a mid-show provider dropout is split across
    <output>.ts plus <output>.part2.ts, <output>.part3.ts, ... (written by
    record_stream() in scripts/record_scheduled.py).
    """
    primary = Path(primary_path)
    parts = sorted(
        primary.parent.glob(f"{primary.stem}.part*.ts"),
        key=lambda p: int(_PART_RE.search(p.name).group(1)),
    )
    # A retry that never got data from the provider leaves an empty file; skip it.
    return [seg for seg in [primary, *parts] if seg.exists() and seg.stat().st_size > 0]


def _join_mp4(parts: list[Path], output: Path) -> tuple[bool, str]:
    """Stream-copy join of MP4 parts. Returns (ok, error text)."""
    list_file = output.with_name(f"{output.stem}.concat_list.txt")
    quoted = lambda p: str(p.resolve()).replace("'", "'\\''")
    list_file.write_text("".join(f"file '{quoted(p)}'\n" for p in parts))
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-map", "0", "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        list_file.unlink(missing_ok=True)
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        lines = result.stderr.strip().splitlines()
        return False, lines[-1] if lines else f"ffmpeg exit {result.returncode}"
    return True, ""


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
    ts_path: str,
    show_dir: str | None = None,
    show_name: str = "Occupation Double",
    report=None,
    planned_seconds: int | None = None,
    air_date: date | None = None,
) -> dict:
    """Convert, join, check length, place into the library, rescan Jellyfin.

    Returns a dict describing what happened at each step; never raises.
    Keys: remux, join, length, placement, jellyfin_scan (None = not reached).
    """
    show_dir = show_dir or config.Config.NOOVO_LIBRARY_DIR
    result = {"remux": None, "join": None, "length": None, "placement": None, "jellyfin_scan": None}

    primary = Path(ts_path)
    segments = find_segments(ts_path) if primary.suffix.lower() == ".ts" else ([primary] if primary.exists() else [])
    if not segments:
        result["remux"] = {"success": False, "filepath": ts_path, "message": "Recording file not found"}
        _step(report, "convert", FAILED, f"recording file not found: {primary.name}")
        return result

    # 1. Convert every piece on its own. Audio is re-encoded, which copes with
    #    the damaged packets a provider dropout leaves at the cut.
    converted: list[Path] = []
    converted_sources: list[Path] = []
    for index, seg in enumerate(segments, start=1):
        r = remux.remux_for_tv(str(seg), keep_source=True)
        if r.get("success"):
            converted.append(Path(r["filepath"]))
            converted_sources.append(seg)
        else:
            _step(report, "convert", FAILED,
                  f"piece {index} of {len(segments)} ({seg.name}) could not be converted: {r.get('message')}")
    if not converted:
        result["remux"] = {"success": False, "filepath": str(segments[0]),
                           "message": "No piece could be converted"}
        _step(report, "convert", FAILED, "no piece could be converted; originals kept")
        return result
    if len(converted) < len(segments):
        result["remux"] = {"success": False, "filepath": str(converted[0]),
                           "message": f"only {len(converted)} of {len(segments)} pieces converted"}
        _step(report, "convert", FAILED,
              f"only {len(converted)} of {len(segments)} pieces converted, so footage would be missing; "
              f"nothing was placed in the library and all pieces were kept in {primary.parent}")
        return result
    else:
        _step(report, "convert", OK, f"converted {len(converted)} piece(s) to MP4")

    # 2. Join the pieces.
    if len(converted) == 1:
        final = converted[0]
        result["remux"] = {"success": True, "filepath": str(final), "message": "Remuxed to MP4"}
    else:
        joined = primary.with_name(f"{primary.stem}.joined.mp4")
        ok, error = _join_mp4(converted, joined)
        if not ok:
            result["remux"] = {"success": True, "filepath": str(converted[0]), "message": "Pieces converted"}
            result["join"] = {"success": False, "message": error}
            _step(report, "join", FAILED,
                  f"joining {len(converted)} pieces failed ({error}); nothing was placed in the library, "
                  f"all pieces kept in {primary.parent}")
            return result
        final = joined
        result["remux"] = {"success": True, "filepath": str(final), "message": "Joined and remuxed to MP4"}
        result["join"] = {"success": True, "pieces": len(converted)}
        _step(report, "join", OK, f"joined {len(converted)} pieces (provider dropout)")

    # 3. Length check against what was planned.
    actual = media_seconds(final)
    result["length"] = {"actual_seconds": actual, "planned_seconds": planned_seconds}
    if actual is None:
        _step(report, "length", WARNING, "could not measure the finished file's length")
    elif planned_seconds:
        ratio = actual / planned_seconds
        text = (f"{format_seconds(actual)} recorded of {format_seconds(planned_seconds)} planned "
                f"({ratio:.0%})")
        status = OK if ratio >= LENGTH_OK else WARNING if ratio >= LENGTH_FAILED else FAILED
        _step(report, "length", status, text, actual_seconds=round(actual),
              planned_seconds=planned_seconds)
    else:
        _step(report, "length", OK, f"{format_seconds(actual)} recorded", actual_seconds=round(actual))

    # 4. Place in the library.
    try:
        target = _next_episode_path(Path(show_dir), show_name, air_date or date.today())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(final), str(target))
    except OSError as exc:
        result["placement"] = {"success": False, "message": str(exc)}
        _step(report, "place", FAILED, f"could not place the episode in the library: {exc}; originals kept")
        return result
    result["placement"] = {"success": True, "path": str(target)}
    _step(report, "place", OK, f"placed episode at {target}", path=str(target))

    # The episode is safe: clean up the pieces that went into it. An original
    # that could not be converted stays, so its footage is not lost.
    for leftover in [*converted_sources, *converted]:
        leftover.unlink(missing_ok=True)

    # 5. Tell Jellyfin.
    scanned, why = scan_library()
    result["jellyfin_scan"] = {"success": scanned, **({} if scanned else {"message": why})}
    if scanned:
        _step(report, "jellyfin", OK, "Jellyfin library scan started")
    else:
        _step(report, "jellyfin", WARNING,
              f"{why}; the episode is in the library but was not scanned")
    return result


def scan_library() -> tuple[bool, str]:
    """Ask Jellyfin to rescan its library. Returns (ok, reason when not ok).

    Prefers the API key (survives password changes); falls back to the login.
    """
    c = config.Config
    if c.JELLYFIN_API_KEY:
        token = c.JELLYFIN_API_KEY
    elif c.JELLYFIN_USERNAME and c.JELLYFIN_PASSWORD:
        token = jellyfin.authenticate(c.JELLYFIN_PUBLISHED_SERVER_URL, c.JELLYFIN_USERNAME, c.JELLYFIN_PASSWORD)
        if not token:
            return False, "Jellyfin rejected the login"
    else:
        return False, "no Jellyfin API key or login configured"
    if jellyfin.trigger_library_scan(c.JELLYFIN_PUBLISHED_SERVER_URL, token):
        return True, ""
    return False, "Jellyfin did not accept the scan request"
