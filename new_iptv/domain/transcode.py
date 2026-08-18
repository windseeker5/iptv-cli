"""Convert a downloaded video file to a profile any TV can play directly.

H.264/1080p/30fps/AAC — no 4K, no 60fps, no HEVC/AV1 that cheap TV SoCs
choke on. This is the same profile Jellyfin itself transcodes to on the fly
for incompatible devices; doing it once at download time means Jellyfin can
direct-play these files forever after.
"""

import subprocess
from pathlib import Path

TV_SAFE_VIDEO_ARGS = [
    "-vf", "scale='min(1920,iw)':'min(1080,ih)':force_original_aspect_ratio=decrease",
    "-r", "30",
    "-pix_fmt", "yuv420p",  # forces 8-bit 4:2:0 — some 4K/HDR sources are 10-bit or 4:4:4,
                             # which High profile (and most cheap TVs) can't decode
    "-c:v", "libx264",
    "-profile:v", "high",
    "-level", "4.1",
    "-preset", "medium",
    "-crf", "21",
    "-c:a", "aac",
    "-b:a", "160k",
    "-movflags", "+faststart",
]


def _probe_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def transcode_to_tv_compatible(input_path: str, on_progress=None, on_pid=None) -> dict:
    """Convert a video file to H.264/1080p/30fps/AAC MP4, replacing the original.

    `on_progress`, if given, is called with an int percent (0-100) as the
    encode proceeds. `on_pid`, if given, is called with the ffmpeg process's
    pid right after it starts (so callers can track/cancel it). Returns a
    dict with keys: success, message, output_path.
    """
    src = Path(input_path)
    if not src.exists():
        return {"success": False, "message": "Source file not found"}

    duration = _probe_duration(str(src))
    temp_output = src.with_name(f"{src.stem}.tvsafe.mp4")

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-progress", "pipe:1", "-nostats",
        *TV_SAFE_VIDEO_ARGS,
        str(temp_output),
    ]

    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        if on_pid:
            on_pid(process.pid)
        for line in process.stdout:
            if line.startswith("out_time_us=") and duration > 0 and on_progress:
                try:
                    seconds = int(line.strip().split("=", 1)[1]) / 1_000_000
                    pct = max(0, min(100, int(seconds / duration * 100)))
                    on_progress(pct)
                except (ValueError, ZeroDivisionError):
                    pass
        stderr_output = process.stderr.read()
        returncode = process.wait()
    except FileNotFoundError:
        return {"success": False, "message": "ffmpeg not installed"}
    except Exception as e:
        return {"success": False, "message": str(e)}

    if returncode != 0 or not temp_output.exists() or temp_output.stat().st_size == 0:
        temp_output.unlink(missing_ok=True)
        last_error = stderr_output.strip().splitlines()[-1] if stderr_output.strip() else ""
        return {"success": False, "message": f"Conversion failed: {last_error or f'ffmpeg exit {returncode}'}"}

    final_output = src.with_suffix(".mp4")
    src.unlink(missing_ok=True)
    temp_output.replace(final_output)

    if on_progress:
        on_progress(100)

    return {"success": True, "message": "Converted for TV playback", "output_path": str(final_output)}
