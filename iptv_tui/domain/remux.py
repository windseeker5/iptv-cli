"""Remux a downloaded video into an MP4 container any TV can direct-play.

Stream copy only — no re-encode. Video/audio bits, frame rate, and profile
level are left exactly as the provider sent them; only the container
changes (e.g. .mkv -> .mp4) and any subtitle track is converted to
MP4-compatible soft subs (mov_text). This is deliberately *not* the old
transcode approach (see iptv_tui/domain/transcode.py history) — that did a
full re-encode and forced a higher H.264 level / frame rate than the source,
which caused judder on TV. A stream copy can't introduce that problem.
"""

import subprocess
from pathlib import Path


def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def remux_for_tv(input_path: str) -> dict:
    """Remux a video file to MP4, replacing the original. Returns a dict
    with keys: success, filepath, message."""
    src = Path(input_path)
    if not src.exists():
        return {"success": False, "filepath": input_path, "message": "Source file not found"}

    if src.suffix.lower() == ".mp4":
        return {"success": True, "filepath": str(src), "message": "Already MP4"}

    temp_output = src.with_name(f"{src.stem}.remux.mp4")

    with_subs = [
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:v", "-map", "0:a", "-map", "0:s?",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-movflags", "+faststart",
        str(temp_output),
    ]
    without_subs = [
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:v", "-map", "0:a",
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart",
        str(temp_output),
    ]

    try:
        result = _run_ffmpeg(with_subs)
        if result.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
            temp_output.unlink(missing_ok=True)
            result = _run_ffmpeg(without_subs)
    except FileNotFoundError:
        return {"success": False, "filepath": str(src), "message": "ffmpeg not installed"}
    except Exception as e:
        temp_output.unlink(missing_ok=True)
        return {"success": False, "filepath": str(src), "message": str(e)}

    if result.returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
        temp_output.unlink(missing_ok=True)
        last_error = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        return {
            "success": False,
            "filepath": str(src),
            "message": f"Remux failed: {last_error or f'ffmpeg exit {result.returncode}'}",
        }

    final_output = src.with_suffix(".mp4")
    src.unlink(missing_ok=True)
    temp_output.replace(final_output)

    return {"success": True, "filepath": str(final_output), "message": "Remuxed to MP4"}
