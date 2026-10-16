"""Remux a downloaded video into an MP4 container any TV can direct-play.

Video is stream copy only — no re-encode, so frame rate and H.264 profile
level are left exactly as the provider sent them. This is deliberately
*not* the old transcode approach (see iptv_tui/domain/transcode.py
history) — that did a full re-encode and forced a higher H.264 level /
frame rate than the source, which caused judder on TV. A stream copy can't
introduce that problem.

Audio that is already AAC is copied: re-encoding it makes ffmpeg abort at
the first damaged packet a provider dropout leaves behind, which silently
truncated long recordings. Other codecs are transcoded to AAC (some live
feeds, observed on NOOVO, switch to E-AC3 mid-stream, which Jellyfin's web
client can't direct-play). A conversion counts as done only when the output
is as long as the source, whatever ffmpeg's exit code says. Any subtitle
track is converted to MP4-compatible soft subs (mov_text).
"""

import subprocess
from pathlib import Path


def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800)


def _probe(path: Path) -> tuple[str | None, float | None]:
    """(first audio codec, container duration in seconds); None when unknown."""
    codec = duration = None
    try:
        a = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
             "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=120)
        codec = a.stdout.strip().splitlines()[0] if a.stdout.strip() else None
        d = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=120)
        duration = float(d.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        pass
    return codec, duration


def _complete(output: Path, source_seconds: float | None, returncode: int) -> bool:
    """Did the conversion produce a usable, full-length file?"""
    if not output.exists() or output.stat().st_size == 0:
        return False
    if source_seconds is None:
        return returncode == 0
    _, produced = _probe(output)
    return produced is not None and produced >= source_seconds * 0.98


def remux_for_tv(input_path: str, keep_source: bool = False) -> dict:
    """Remux a video file to MP4, replacing the original unless keep_source is
    set (the recording pipeline keeps it until the episode is safely placed).
    Returns a dict with keys: success, filepath, message."""
    src = Path(input_path)
    if not src.exists():
        return {"success": False, "filepath": input_path, "message": "Source file not found"}

    if src.suffix.lower() == ".mp4":
        return {"success": True, "filepath": str(src), "message": "Already MP4"}

    temp_output = src.with_name(f"{src.stem}.remux.mp4")

    audio_codec, source_seconds = _probe(src)
    copy_audio = audio_codec == "aac"

    def command(subs: bool, copy: bool) -> list[str]:
        cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a"]
        if subs:
            cmd += ["-map", "0:s?"]
        cmd += ["-c:v", "copy"]
        # The explicit AAC filter drops a damaged packet and carries on; without
        # it the muxer aborts the whole file at the first one.
        cmd += ["-c:a", "copy", "-bsf:a", "aac_adtstoasc"] if copy else ["-c:a", "aac", "-b:a", "192k"]
        if subs:
            cmd += ["-c:s", "mov_text"]
        return cmd + ["-movflags", "+faststart", str(temp_output)]

    # Preferred audio first, then subtitles off, then a full audio re-encode as
    # the last resort for a file whose copied audio will not mux.
    attempts = [(True, copy_audio), (False, copy_audio)]
    if copy_audio:
        attempts.append((False, False))

    result = None
    try:
        for subs, copy in attempts:
            temp_output.unlink(missing_ok=True)
            result = _run_ffmpeg(command(subs, copy))
            if _complete(temp_output, source_seconds, result.returncode):
                break
        else:
            temp_output.unlink(missing_ok=True)
    except FileNotFoundError:
        return {"success": False, "filepath": str(src), "message": "ffmpeg not installed"}
    except Exception as e:
        temp_output.unlink(missing_ok=True)
        return {"success": False, "filepath": str(src), "message": str(e)}

    if result is None or not temp_output.exists() or temp_output.stat().st_size == 0:
        last_error = result.stderr.strip().splitlines()[-1] if result and result.stderr.strip() else ""
        return {
            "success": False,
            "filepath": str(src),
            "message": f"Remux failed: {last_error or 'no complete output'}",
        }

    final_output = src.with_suffix(".mp4")
    try:
        temp_output.replace(final_output)
        if not keep_source:
            src.unlink()
    except OSError as exc:
        return {
            "success": False,
            "filepath": str(src),
            "message": f"Could not replace original safely: {exc}",
        }

    return {"success": True, "filepath": str(final_output), "message": "Remuxed to MP4"}
