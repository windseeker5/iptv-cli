"""Registry of download/record jobs for the running app session, persisted
to disk so history survives an app restart.

Scheduled recordings already persist to SQLite (domain.recordings) and series
batches already persist to JSON manifests (domain.downloads); this module only
tracks the jobs that otherwise have no visibility at all (single VOD/live
downloads, YouTube downloads) and merges everything into one unified list for
the UI.
"""

import itertools
import json
import os
import signal
import time

from new_iptv.domain import db, downloads, recordings

_LOGS_DIR = db.data_dir() / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)
_STORE_PATH = _LOGS_DIR / "jobs.json"

_jobs: dict[str, dict] = {}
_id_counter = itertools.count(1)


def _save() -> None:
    try:
        with open(_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(_jobs, f, indent=2)
    except Exception:
        pass


def _load() -> None:
    if not _STORE_PATH.exists():
        return
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    max_n = 0
    for job_id, job in data.items():
        if job.get("status") == "running":
            # The process/thread that owned this job is gone now that we're
            # starting fresh — it can never actually finish.
            job["status"] = "interrupted"
            job["detail"] = "Interrupted by app restart"
            job["pid"] = None
        _jobs[job_id] = job
        if job_id.startswith("job") and job_id[3:].isdigit():
            max_n = max(max_n, int(job_id[3:]))
    global _id_counter
    _id_counter = itertools.count(max_n + 1)


_load()


def register(job_type: str, title: str, pid: int | None = None) -> str:
    """Register a new job and return its id."""
    job_id = f"job{next(_id_counter)}"
    _jobs[job_id] = {
        "id": job_id,
        "type": job_type,
        "title": title,
        "status": "running",
        "pid": pid,
        "detail": "",
        "started_at": time.time(),
        "cancel_requested": False,
    }
    _save()
    return job_id


def cancel_requested(job_id: str) -> bool:
    """Whether a cancel was requested for this job (for cooperative cancellation)."""
    job = _jobs.get(job_id)
    return bool(job and job["cancel_requested"])


def update(job_id: str, **fields) -> None:
    """Update fields on an existing job. No-op if the job is unknown."""
    detail = fields.get("detail")
    if detail:
        detail = " ".join(detail.split())
        if len(detail) > 150:
            detail = detail[:150] + "…"
        fields["detail"] = detail
    job = _jobs.get(job_id)
    if job:
        job.update(fields)
        _save()


def _icon(status: str) -> str:
    return {
        "running": "🟢",
        "pending": "⚪",
        "scheduled": "⚪",
        "done": "✅",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "❌",
        "interrupted": "❌",
    }.get(status, "⚪")


def _in_memory_rows() -> list[dict]:
    rows = []
    for job in _jobs.values():
        elapsed = int(time.time() - job["started_at"])
        detail = job["detail"] or f"{elapsed}s"
        rows.append(
            {
                "job_id": job["id"],
                "kind": "memory",
                "icon": _icon(job["status"]),
                "type": job["type"],
                "title": job["title"],
                "detail": detail,
                "status": job["status"],
                "sort_key": job["started_at"],
            }
        )
    return rows


def _scheduled_rows() -> list[dict]:
    rows = []
    for item in recordings.list_recordings(limit=50):
        status = item.get("status", "pending")
        start_dt = recordings.datetime.fromtimestamp(item["start_time"])
        duration_h = item["duration"] / 3600
        rows.append(
            {
                "job_id": f"rec{item['id']}",
                "kind": "scheduled",
                "recording_id": item["id"],
                "icon": _icon(status),
                "type": "scheduled",
                "title": item["channel_name"],
                "detail": f"{status}  {start_dt.strftime('%Y-%m-%d %H:%M')}  {duration_h:.1f}h",
                "status": status,
                "sort_key": item["start_time"],
            }
        )
    return rows


def _series_rows() -> list[dict]:
    rows = []
    for path in downloads.list_batch_manifests(limit=40):
        state = downloads.read_batch_state(path)
        total = state.get("total", 0)
        completed = state.get("completed", 0)
        failed = state.get("failed", 0)
        if state.get("in_progress"):
            status = "running"
        elif completed + failed >= total and total > 0:
            status = "done" if failed == 0 else "failed"
        else:
            status = "pending"
        try:
            title = downloads._read_manifest(path).get("series_name") or path.stem
        except Exception:
            title = path.stem
        rows.append(
            {
                "job_id": f"series:{path}",
                "kind": "series",
                "manifest_path": path,
                "icon": _icon(status),
                "type": "series",
                "title": title,
                "detail": f"{completed}/{total} done" + (f"  ({failed} failed)" if failed else ""),
                "status": status,
                "sort_key": path.stat().st_mtime,
            }
        )
    return rows


def list_jobs() -> list[dict]:
    """Return all known jobs (in-memory, scheduled, series) newest first."""
    rows = _in_memory_rows() + _scheduled_rows() + _series_rows()
    rows.sort(key=lambda r: r["sort_key"], reverse=True)
    return rows


def reset() -> None:
    """Cancel every running in-memory job and clear the registry."""
    for row in _in_memory_rows():
        if row["status"] == "running":
            cancel(row)
    _jobs.clear()
    _save()


def cancel(row: dict) -> dict:
    """Cancel a job row as returned by list_jobs()."""
    kind = row["kind"]
    if kind == "memory":
        job = _jobs.get(row["job_id"])
        pid = job.get("pid") if job else None
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except Exception as e:
                return {"success": False, "message": f"Failed to stop process: {e}"}
        if job:
            # For jobs with no OS process (e.g. an in-process YouTube
            # download thread), this flag is how the thread notices and
            # aborts itself cooperatively.
            job["cancel_requested"] = True
            job["status"] = "cancelled"
            _save()
        return {"success": True, "message": "Job cancelled"}
    if kind == "scheduled":
        ok = recordings.cancel_recording(row["recording_id"])
        return {"success": ok, "message": "Recording cancelled" if ok else "Recording not found"}
    if kind == "series":
        downloads.cancel_batch(row["manifest_path"])
        return {"success": True, "message": "Series batch cancelled"}
    return {"success": False, "message": "Unknown job type"}
