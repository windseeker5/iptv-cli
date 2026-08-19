"""Execute playback, restream, and download actions without UI dependencies."""

import os
import subprocess
from pathlib import Path

from iptv_tui.domain import downloads, iptv_provider, jobs, recordings, restream


def is_raspberry_pi() -> bool:
    """Detect Raspberry Pi for hardware acceleration options."""
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists() and "Raspberry Pi" in model_path.read_text():
            return True
        cpuinfo_path = Path("/proc/cpuinfo")
        if cpuinfo_path.exists():
            cpuinfo = cpuinfo_path.read_text()
            if "BCM" in cpuinfo or "Raspberry Pi" in cpuinfo:
                return True
    except Exception:
        pass
    return False


def _mpv_base_command() -> list[str]:
    """Build the base mpv command with platform-specific options."""
    cmd = ["mpv", "--no-ytdl"]
    if is_raspberry_pi():
        cmd.extend(["--hwdec=v4l2m2m", "--vo=dmabuf-wayland", "--gpu-context=wayland"])
    else:
        cmd.extend(["--hwdec=auto", "--vo=gpu"])
    cmd.extend(
        [
            "--cache=yes",
            "--cache-secs=30",
            "--demuxer-max-bytes=100M",
            "--demuxer-max-back-bytes=50M",
            "--demuxer-readahead-secs=20",
            "--network-timeout=60",
            "--stream-lavf-o=reconnect=1",
            "--stream-lavf-o=reconnect_at_eof=1",
            "--stream-lavf-o=reconnect_streamed=1",
            "--stream-lavf-o=reconnect_delay_max=30",
            "--keep-open=yes",
            "--osd-level=1",
        ]
    )
    return cmd


def play_url(url: str) -> dict:
    """Play a stream URL in MPV, detached from the terminal."""
    if not url:
        return {"success": False, "message": "No URL to play"}

    try:
        subprocess.run(["mpv", "--version"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {"success": False, "message": "MPV not found. Install mpv to play streams."}

    cmd = _mpv_base_command() + [url]
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {
            "success": True,
            "pid": process.pid,
            "message": "MPV started",
        }
    except Exception as e:
        return {"success": False, "message": f"Failed to start MPV: {e}"}


def play_item(item: dict, item_type: str) -> dict:
    """Play a live channel or VOD item."""
    url = item.get("stream_url")
    if not url and item_type == "live":
        url = iptv_provider.build_stream_url(item.get("stream_id", 0))
    if not url:
        return {"success": False, "message": "No stream URL available"}
    return play_url(url)


def play_youtube_video(url: str) -> dict:
    """Play a YouTube URL with MPV using yt-dlp."""
    try:
        subprocess.run(["mpv", "--version"], capture_output=True, check=True, timeout=5)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {"success": False, "message": "MPV not found"}

    cmd = [
        "mpv",
        "--ytdl-format=bestvideo[height<=1080]+bestaudio/best",
    ]
    if is_raspberry_pi():
        cmd.extend(["--hwdec=v4l2m2m", "--vo=dmabuf-wayland", "--gpu-context=wayland"])
    else:
        cmd.extend(["--hwdec=auto", "--vo=gpu"])
    cmd.extend(
        [
            "--cache=yes",
            "--demuxer-max-bytes=100M",
            "--demuxer-max-back-bytes=50M",
            "--network-timeout=60",
            "--keep-open=yes",
            url,
        ]
    )

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"success": True, "pid": process.pid, "message": "MPV started"}
    except Exception as e:
        return {"success": False, "message": f"Failed to start MPV: {e}"}


def restream_item(item: dict) -> dict:
    """Start restreaming an item to NGINX-RTMP."""
    return restream.start_restream(item)


def stop_active_restream() -> dict:
    """Stop the active restream."""
    return restream.stop_restream()


def record_live_item(item: dict, duration_seconds: int = 3600) -> dict:
    """Start recording a live stream."""
    job_id = jobs.register("live", item.get("name", "Unknown"))

    def on_done(success: bool, error: str | None) -> None:
        jobs.update(job_id, status="done" if success else "failed", detail=error or "")

    result = downloads.start_live_download(item, duration_seconds, on_done=on_done)
    if result["success"]:
        jobs.update(job_id, pid=result.get("pid"))
    else:
        jobs.update(job_id, status="failed", detail=result["message"])
    return result


def schedule_live_recording(item: dict, start_input: str, duration_minutes: int) -> dict:
    """Schedule a recording for a live channel."""
    try:
        start_time = recordings.parse_start_time(start_input)
    except ValueError as e:
        return {"success": False, "message": str(e)}

    return recordings.schedule_recording(
        stream_id=item.get("stream_id", 0),
        channel_name=item.get("name", "Unknown"),
        start_time=start_time,
        duration_seconds=duration_minutes * 60,
    )


def download_vod_item(item: dict, tv_compatible: bool = False) -> dict:
    """Start downloading a VOD item, optionally converting it for TV playback after."""
    job_id = jobs.register("vod", downloads.item_display_name(item))

    def on_done(success: bool, error: str | None) -> None:
        jobs.update(job_id, status="done" if success else "failed", detail=error or "")

    def on_progress(pct: int) -> None:
        jobs.update(job_id, detail=f"converting {pct}%")

    def on_pid(pid: int) -> None:
        jobs.update(job_id, pid=pid)

    result = downloads.start_vod_download(
        item,
        on_done=on_done,
        tv_compatible=tv_compatible,
        on_progress=on_progress if tv_compatible else None,
        on_pid=on_pid if tv_compatible else None,
    )
    if result["success"]:
        jobs.update(job_id, pid=result.get("pid"))
    else:
        jobs.update(job_id, status="failed", detail=result["message"])
    return result


def download_series(item: dict, tv_compatible: bool = False) -> dict:
    """Queue all episodes of a series for download, optionally converting each for TV playback."""
    series_id = item.get("series_id")
    if not series_id:
        return {"success": False, "message": "No series ID"}
    episodes = iptv_provider.get_series_episodes(series_id)
    if not episodes:
        return {"success": False, "message": "No episodes found"}
    return downloads.queue_series_batch(item, episodes, tv_compatible=tv_compatible)
