"""Download helpers for live streams, VOD, and series batches."""

import glob
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from new_iptv.domain import config, db


def _downloads_dir() -> Path:
    path = db.data_dir() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _records_dir() -> Path:
    path = Path(config.Config.USB_RECORDS_PATH)
    if path.exists() and os.access(path, os.W_OK):
        return path
    fallback = db.data_dir() / "recordings"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _safe_name(name: str) -> str:
    safe = "".join(c for c in name if c.isalnum() or c in (" ", "-", "_")).rstrip()
    return re.sub(r"\s+", " ", safe).replace(" ", "_")


def _get_download_extension(item: dict) -> str:
    """Infer file extension from stream URL or container_extension."""
    url = item.get("stream_url", "")
    ext = item.get("container_extension", "")
    if ext:
        return ext.lstrip(".")
    if url:
        return Path(url.split("?")[0]).suffix.lstrip(".") or "mp4"
    return "mp4"


def detect_downloader() -> str:
    """Return 'wget', 'curl', or 'python' based on available tools."""
    try:
        subprocess.run(["wget", "--version"], capture_output=True, check=True, timeout=5)
        return "wget"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        subprocess.run(["curl", "--version"], capture_output=True, check=True, timeout=5)
        return "curl"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "python"


def _download_with_requests(url: str, filepath: str) -> bool:
    """Download a URL to a file using Python requests in a background thread."""
    def thread():
        try:
            headers = {"User-Agent": "VLC/3.0.0 LibVLC/3.0.0"}
            with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            print(f"Download failed: {e}")

    t = threading.Thread(target=thread, daemon=True)
    t.start()
    return True


def build_download_command(url: str, filepath: str, downloader: str) -> list[str]:
    """Build a download command list for wget or curl."""
    if downloader == "wget":
        return [
            "wget",
            "-O",
            filepath,
            "--user-agent=VLC/3.0.0 LibVLC/3.0.0",
            "--header=Accept: */*",
            "--header=Connection: keep-alive",
            "--timeout=30",
            "--tries=3",
            url,
        ]
    if downloader == "curl":
        return [
            "curl",
            "-o",
            filepath,
            "-A",
            "VLC/3.0.0 LibVLC/3.0.0",
            "-H",
            "Accept: */*",
            "-H",
            "Connection: keep-alive",
            "--connect-timeout",
            "30",
            "--max-time",
            "0",
            "-L",
            url,
        ]
    raise ValueError(f"Unsupported downloader: {downloader}")


def start_vod_download(item: dict, output_dir: str | None = None) -> dict:
    """Start a VOD download in the background.

    Returns dict with success, pid or thread, filepath, message.
    """
    url = item.get("stream_url", "")
    if not url:
        return {"success": False, "message": "No stream URL available"}

    ext = _get_download_extension(item)
    filename = f"{_safe_name(item.get('name', 'unknown'))}.{ext}"
    folder = Path(output_dir) if output_dir else _downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    filepath = str(folder / filename)

    downloader = detect_downloader()

    if downloader == "python":
        _download_with_requests(url, filepath)
        return {
            "success": True,
            "filepath": filepath,
            "message": "Download started in background thread",
        }

    cmd = build_download_command(url, filepath, downloader)
    try:
        process = subprocess.Popen(cmd)
        return {
            "success": True,
            "pid": process.pid,
            "filepath": filepath,
            "message": "Download started",
        }
    except Exception as e:
        return {"success": False, "message": f"Failed to start download: {e}"}


def start_live_download(item: dict, duration_seconds: int = 3600) -> dict:
    """Start recording a live stream to disk."""
    url = item.get("stream_url", "")
    if not url:
        return {"success": False, "message": "No stream URL available"}

    folder = _records_dir()
    filename = f"{_safe_name(item.get('name', 'unknown'))}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.ts"
    filepath = str(folder / filename)

    cmd = [
        "ffmpeg",
        "-i",
        url,
        "-c",
        "copy",
        "-t",
        str(duration_seconds),
        filepath,
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {
            "success": True,
            "pid": process.pid,
            "filepath": filepath,
            "message": "Recording started",
        }
    except FileNotFoundError:
        return {"success": False, "message": "FFmpeg not installed"}
    except Exception as e:
        return {"success": False, "message": f"Recording failed: {e}"}


# Series batch downloads


def _manifest_path(series_name: str, timestamp: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", series_name.replace(" ", "_"))[:40]
    return db.data_dir() / f"series_batch_{safe}_{timestamp}.json"


def _log_path(manifest_path: Path) -> Path:
    logs_dir = db.data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir / f"{manifest_path.stem}.log"


def queue_series_batch(series_item: dict, episodes: list[dict], batch_label: str | None = None) -> dict:
    """Create a manifest and launch a background series batch download.

    Returns dict with success, manifest_path, log_path, message.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    series_name = series_item.get("name", "Unknown")
    manifest = _manifest_path(series_name, timestamp)
    log = _log_path(manifest)

    jobs = []
    for episode in episodes:
        ext = episode.get("container_extension", "mp4").lstrip(".") or "mp4"
        filename = f"{_safe_name(series_name)}_S{episode.get('season_number', 0):02d}E{episode.get('episode_num', 0):02d}.{ext}"
        jobs.append(
            {
                "episode_id": episode.get("episode_id"),
                "title": episode.get("title"),
                "season_number": episode.get("season_number"),
                "episode_num": episode.get("episode_num"),
                "stream_url": episode.get("stream_url"),
                "filename": filename,
                "status": "pending",
            }
        )

    manifest_data = {
        "series_id": series_item.get("series_id"),
        "series_name": series_name,
        "batch_label": batch_label,
        "created_at": timestamp,
        "process_pid": None,
        "jobs": jobs,
    }

    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)

    # Launch a background downloader process (self-contained script)
    downloader_script = Path(__file__).resolve().parent / "_series_batch_worker.py"
    if downloader_script.exists():
        try:
            process = subprocess.Popen(
                ["python3", str(downloader_script), str(manifest), str(log)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            manifest_data["process_pid"] = process.pid
            with open(manifest, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2)
        except Exception as e:
            return {"success": False, "message": f"Failed to launch worker: {e}"}

    return {
        "success": True,
        "manifest_path": str(manifest),
        "log_path": str(log),
        "message": f"Queued {len(jobs)} episodes",
    }


def list_batch_manifests(limit: int = 40) -> list[Path]:
    """Return series batch manifest files, newest first."""
    pattern = str(db.data_dir() / "series_batch_*.json")
    files = sorted(Path(p) for p in glob.glob(pattern))
    return files[-limit:][::-1]


def read_batch_state(manifest_path: Path) -> dict:
    """Read a manifest and infer status from its log file."""
    state = {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "in_progress": False,
        "process_pid": None,
    }
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        state["total"] = len(jobs)
        state["process_pid"] = data.get("process_pid")
        for job in jobs:
            status = job.get("status", "pending")
            if status == "completed":
                state["completed"] += 1
            elif status == "failed":
                state["failed"] += 1

        log = _log_path(manifest_path)
        if log.exists():
            text = log.read_text(encoding="utf-8", errors="ignore")
            state["completed"] = text.count("[DONE]")
            state["failed"] = text.count("[FAIL]")
            state["in_progress"] = "[START]" in text and state["completed"] < state["total"]
    except Exception:
        pass
    return state
