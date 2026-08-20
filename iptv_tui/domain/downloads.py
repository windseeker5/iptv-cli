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

from iptv_tui.domain import config, db

# Guards the actual network transfer so only one download (single VOD or
# series-batch episode) is ever pulling data from the provider at once —
# it only allows one concurrent connection, so parallel downloads knock
# each other off.
_transfer_lock = threading.Lock()


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


def item_display_name(item: dict) -> str:
    """Best available human-readable name: VOD items have 'name', series
    episodes have 'title' (+ season/episode numbers) instead."""
    name = item.get("name")
    if name:
        return name
    title = item.get("title")
    season = item.get("season_number")
    episode = item.get("episode_num")
    if title and season is not None and episode is not None:
        return f"S{season:02d}E{episode:02d} - {title}"
    return title or "unknown"


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


def _wait_and_report(process: subprocess.Popen, on_done) -> None:
    """Wait for a subprocess to exit in a background thread and report the result."""
    def thread():
        returncode = process.wait()
        if returncode == 0:
            on_done(True, None)
        else:
            on_done(False, f"Exited with code {returncode}")

    threading.Thread(target=thread, daemon=True).start()


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


def _download_worker(url: str, filepath: str, on_start=None, on_done=None, cancel_check=None) -> None:
    """Run one VOD transfer, holding `_transfer_lock` for its full duration
    so it never overlaps another download."""
    with _transfer_lock:
        if cancel_check and cancel_check():
            if on_done:
                on_done(False, "Cancelled")
            return
        downloader = detect_downloader()
        try:
            if downloader == "python":
                if on_start:
                    on_start(None)
                headers = {"User-Agent": "VLC/3.0.0 LibVLC/3.0.0"}
                with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
                    resp.raise_for_status()
                    with open(filepath, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                if on_done:
                    on_done(True, None)
            else:
                cmd = build_download_command(url, filepath, downloader)
                process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if on_start:
                    on_start(process.pid)
                returncode = process.wait()
                if returncode == 0:
                    if on_done:
                        on_done(True, None)
                else:
                    if on_done:
                        on_done(False, f"Exited with code {returncode}")
        except Exception as e:
            if on_done:
                on_done(False, str(e))


def queue_vod_download(
    item: dict,
    output_dir: str | None = None,
    on_start=None,
    on_done=None,
    cancel_check=None,
) -> dict:
    """Queue a VOD download to run as soon as any earlier queued download finishes.

    `on_start`, if given, is called as `on_start(pid: int | None)` right
    before the transfer actually begins (pid is `None` for the python/
    requests fallback). `on_done`, if given, is called as
    `on_done(success: bool, error: str | None)` once the download finishes.
    `cancel_check`, if given, is polled right before the transfer starts so
    a download cancelled while still queued never starts at all.

    Returns dict with success, filepath, message.
    """
    url = item.get("stream_url", "")
    if not url:
        return {"success": False, "message": "No stream URL available"}

    ext = _get_download_extension(item)
    filename = f"{_safe_name(item_display_name(item))}.{ext}"
    folder = Path(output_dir) if output_dir else _downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    filepath = str(folder / filename)

    threading.Thread(
        target=_download_worker,
        args=(url, filepath),
        kwargs={"on_start": on_start, "on_done": on_done, "cancel_check": cancel_check},
        daemon=True,
    ).start()

    return {
        "success": True,
        "filepath": filepath,
        "message": "Download queued",
    }


def start_live_download(item: dict, duration_seconds: int = 3600, on_done=None) -> dict:
    """Start recording a live stream to disk.

    `on_done`, if given, is called as `on_done(success: bool, error: str | None)`
    once the recording finishes (from a background thread).
    """
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
        if on_done:
            _wait_and_report(process, on_done)
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


def _write_manifest(manifest: Path, data: dict) -> None:
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _read_manifest(manifest: Path) -> dict:
    with open(manifest, "r", encoding="utf-8") as f:
        return json.load(f)


def _run_series_batch(manifest: Path, series_name: str) -> None:
    """Download each episode in a manifest sequentially, updating status as it goes."""
    downloader = detect_downloader()
    folder = _downloads_dir() / _safe_name(series_name)
    folder.mkdir(parents=True, exist_ok=True)

    data = _read_manifest(manifest)
    for job in data["jobs"]:
        # Re-read the manifest each iteration so a cancel request is picked up.
        data = _read_manifest(manifest)
        if data.get("cancelled"):
            break

        current = next((j for j in data["jobs"] if j["episode_id"] == job["episode_id"]), job)
        current["status"] = "queued"
        _write_manifest(manifest, data)

        url = current.get("stream_url")
        filepath = str(folder / current["filename"])
        ok = False
        if url:
            with _transfer_lock:
                data = _read_manifest(manifest)
                if data.get("cancelled"):
                    break
                current = next(
                    (j for j in data["jobs"] if j["episode_id"] == job["episode_id"]), current
                )
                current["status"] = "running"
                _write_manifest(manifest, data)
                try:
                    if downloader == "python":
                        headers = {"User-Agent": "VLC/3.0.0 LibVLC/3.0.0"}
                        with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
                            resp.raise_for_status()
                            with open(filepath, "wb") as f:
                                for chunk in resp.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                        ok = True
                    else:
                        cmd = build_download_command(url, filepath, downloader)
                        result = subprocess.run(cmd, capture_output=True, timeout=1800)
                        ok = result.returncode == 0
                except Exception:
                    ok = False

        data = _read_manifest(manifest)
        current = next((j for j in data["jobs"] if j["episode_id"] == job["episode_id"]), None)
        if current:
            current["status"] = "completed" if ok else "failed"
            _write_manifest(manifest, data)


def queue_series_batch(
    series_item: dict,
    episodes: list[dict],
    batch_label: str | None = None,
) -> dict:
    """Create a manifest and download all episodes sequentially in a background thread.

    Returns dict with success, manifest_path, message.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    series_name = series_item.get("name", "Unknown")
    manifest = _manifest_path(series_name, timestamp)

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
        "cancelled": False,
        "jobs": jobs,
    }
    _write_manifest(manifest, manifest_data)

    threading.Thread(
        target=_run_series_batch, args=(manifest, series_name), daemon=True
    ).start()

    return {
        "success": True,
        "manifest_path": str(manifest),
        "message": f"Queued {len(jobs)} episodes",
    }


def cancel_batch(manifest_path: Path) -> None:
    """Signal a running series batch thread to stop after its current episode."""
    try:
        data = _read_manifest(manifest_path)
        data["cancelled"] = True
        _write_manifest(manifest_path, data)
    except Exception:
        pass


def list_batch_manifests(limit: int = 40) -> list[Path]:
    """Return series batch manifest files, newest first."""
    pattern = str(db.data_dir() / "series_batch_*.json")
    files = sorted(Path(p) for p in glob.glob(pattern))
    return files[-limit:][::-1]


def read_batch_state(manifest_path: Path) -> dict:
    """Read a manifest and summarize job status counts."""
    state = {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "in_progress": False,
    }
    try:
        data = _read_manifest(manifest_path)
        jobs = data.get("jobs", [])
        state["total"] = len(jobs)
        for job in jobs:
            status = job.get("status", "pending")
            if status == "completed":
                state["completed"] += 1
            elif status == "failed":
                state["failed"] += 1
            elif status in ("running", "queued"):
                state["in_progress"] = True
    except Exception:
        pass
    return state
