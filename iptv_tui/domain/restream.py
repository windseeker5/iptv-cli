"""Restream management using FFmpeg and NGINX-RTMP."""

import glob
import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from iptv_tui.domain import config, db


def _data_dir() -> Path:
    return db.data_dir()


def meta_file() -> Path:
    """Path to the active restream metadata file."""
    return _data_dir() / ".restream_active.json"


def generate_stream_key(name: str) -> str:
    """Generate a URL-safe stream key from a channel name."""
    key = re.sub(r"[^a-zA-Z0-9_-]", "", name.replace(" ", "_").lower())
    return key or "stream"


def _is_raspberry_pi() -> bool:
    """Detect if running on a Raspberry Pi."""
    try:
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text()
            if "Raspberry Pi" in model:
                return True
        cpuinfo_path = Path("/proc/cpuinfo")
        if cpuinfo_path.exists():
            cpuinfo = cpuinfo_path.read_text()
            if "BCM" in cpuinfo or "Raspberry Pi" in cpuinfo:
                return True
    except Exception:
        pass
    return False


def build_ffmpeg_command(source_url: str, stream_key: str, transcode: bool = False) -> list[str]:
    """Build an FFmpeg command for restreaming to NGINX-RTMP."""
    target_url = f"rtmp://localhost:{config.Config.NGINX_RTMP_PORT}/live/{stream_key}"

    if transcode:
        if _is_raspberry_pi():
            return [
                "ffmpeg",
                "-hwaccel",
                "v4l2m2m",
                "-i",
                source_url,
                "-c:v",
                "h264_v4l2m2m",
                "-b:v",
                "1M",
                "-maxrate",
                "1M",
                "-bufsize",
                "2M",
                "-vf",
                "scale=854:480",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-f",
                "flv",
                target_url,
            ]
        return [
            "ffmpeg",
            "-i",
            source_url,
            "-c:v",
            "libx264",
            "-preset",
            "superfast",
            "-tune",
            "zerolatency",
            "-b:v",
            "1M",
            "-maxrate",
            "1M",
            "-bufsize",
            "2M",
            "-vf",
            "scale=854:480",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-f",
            "flv",
            target_url,
        ]

    return [
        "ffmpeg",
        "-reconnect",
        "1",
        "-reconnect_at_eof",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "30",
        "-fflags",
        "+genpts+discardcorrupt",
        "-rtbufsize",
        "15M",
        "-analyzeduration",
        "10M",
        "-probesize",
        "10M",
        "-i",
        source_url,
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-max_muxing_queue_size",
        "9999",
        "-f",
        "flv",
        target_url,
    ]


def save_restream_meta(stream_key: str, channel_name: str, pid: int, source_url: str) -> None:
    """Write active restream metadata to disk."""
    data = {
        "stream_key": stream_key,
        "channel_name": channel_name,
        "pid": pid,
        "source_url": source_url,
        "started_at": datetime.now().isoformat(),
    }
    with open(meta_file(), "w", encoding="utf-8") as f:
        json.dump(data, f)


def clear_restream_meta() -> None:
    """Remove active restream metadata file."""
    try:
        meta_file().unlink(missing_ok=True)
    except Exception:
        pass


def detect_running_restream() -> dict | None:
    """Detect a running FFmpeg restream process by inspecting command lines."""
    try:
        result = subprocess.run(
            ["pgrep", "-a", "ffmpeg"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        for line in result.stdout.splitlines():
            if "rtmp://localhost" in line and "/live/" in line:
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue

                cmd = parts[1]
                match = re.search(r"rtmp://[^/]+/live/([^\s]+)", cmd)
                stream_key = match.group(1) if match else "unknown"

                source_url = "Unknown"
                if " -i " in cmd:
                    source_match = re.search(r" -i (\S+)", cmd)
                    if source_match:
                        source_url = source_match.group(1)

                return {
                    "stream_key": stream_key,
                    "channel_name": f"[Recovered] {stream_key}",
                    "pid": pid,
                    "source_url": source_url,
                    "started_at": None,
                    "recovered": True,
                }
    except Exception:
        pass
    return None


def get_active_restream() -> dict | None:
    """Return active restream info or None."""
    path = meta_file()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            os.kill(data["pid"], 0)
            return data
        except (ProcessLookupError, json.JSONDecodeError, KeyError, FileNotFoundError):
            clear_restream_meta()

    detected = detect_running_restream()
    if detected:
        save_restream_meta(
            detected["stream_key"],
            detected["channel_name"],
            detected["pid"],
            detected["source_url"],
        )
        return {**detected, "recovered": True}
    return None


def start_restream(
    item: dict, stream_key: str | None = None, transcode: bool = False
) -> dict:
    """Start a restream for the given item.

    Returns a dict with success, pid, stream_key, hls_url, rtmp_url, message.
    """
    source_url = item.get("stream_url") or item.get("url", "")
    if not source_url:
        return {"success": False, "message": "No stream URL available"}

    channel_name = item.get("name", "Unknown")
    stream_key = stream_key or generate_stream_key(channel_name)

    ffmpeg_cmd = build_ffmpeg_command(source_url, stream_key, transcode)

    try:
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        time.sleep(1)
        if process.poll() is not None:
            return {"success": False, "message": "FFmpeg process exited immediately"}

        save_restream_meta(stream_key, channel_name, process.pid, source_url)
        return {
            "success": True,
            "pid": process.pid,
            "stream_key": stream_key,
            "channel_name": channel_name,
            "hls_url": f"http://localhost:{config.Config.NGINX_HTTP_PORT}/hls/{stream_key}.m3u8",
            "rtmp_url": f"rtmp://localhost:{config.Config.NGINX_RTMP_PORT}/live/{stream_key}",
            "message": "Restream started",
        }
    except Exception as e:
        return {"success": False, "message": f"Failed to start restream: {e}"}


def stop_restream() -> dict:
    """Stop the active restream and clean up metadata/PID files."""
    active = get_active_restream()
    stopped = []

    if active:
        try:
            os.kill(active["pid"], signal.SIGTERM)
            stopped.append(active["channel_name"])
        except ProcessLookupError:
            pass
        clear_restream_meta()

    # Fallback: old-style PID files
    pid_files = glob.glob(str(_data_dir() / ".restream_*.pid"))
    for pid_file in pid_files:
        try:
            with open(pid_file, "r", encoding="utf-8") as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
            stopped.append(str(pid))
        except (OSError, ValueError, ProcessLookupError):
            pass
        try:
            os.remove(pid_file)
        except Exception:
            pass

    return {
        "success": bool(stopped),
        "stopped": stopped,
        "message": f"Stopped {len(stopped)} restream(s)" if stopped else "No active restreams",
    }


def stream_urls(stream_key: str) -> dict:
    """Return viewing URLs for a stream key."""
    return {
        "hls": f"http://localhost:{config.Config.NGINX_HTTP_PORT}/hls/{stream_key}.m3u8",
        "rtmp": f"rtmp://localhost:{config.Config.NGINX_RTMP_PORT}/live/{stream_key}",
    }
