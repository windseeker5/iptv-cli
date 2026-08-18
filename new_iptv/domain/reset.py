"""Wipe accumulated download/recording history: files, manifests, logs, DB rows."""

from pathlib import Path

from new_iptv.domain import db, downloads, jobs, recordings, youtube


def _dir_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for a directory's contents."""
    if not path.exists():
        return 0, 0
    count = 0
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            count += 1
            total += f.stat().st_size
    return count, total


def _clear_dir(path: Path) -> None:
    """Delete a directory's contents, keeping the directory itself."""
    if not path.exists():
        return
    for f in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if f.is_file():
            f.unlink(missing_ok=True)
        elif f.is_dir():
            try:
                f.rmdir()
            except OSError:
                pass


def preview() -> dict:
    """Read-only summary of what clear_all() would remove."""
    dl_count, dl_bytes = _dir_stats(downloads._downloads_dir())
    yt_count, yt_bytes = _dir_stats(youtube.YOUTUBE_DIR)
    rec_count, rec_bytes = _dir_stats(downloads._records_dir())
    manifests = downloads.list_batch_manifests(limit=1000)
    logs = list((db.data_dir() / "logs").glob("series_batch_*.log"))
    scheduled = recordings.list_recordings(limit=1000)

    return {
        "downloads": {"count": dl_count, "bytes": dl_bytes},
        "youtube": {"count": yt_count, "bytes": yt_bytes},
        "recordings": {"count": rec_count, "bytes": rec_bytes},
        "manifests": len(manifests),
        "logs": len(logs),
        "scheduled": len(scheduled),
        "total_bytes": dl_bytes + yt_bytes + rec_bytes,
    }


def clear_all() -> dict:
    """Delete every download/recording file plus all tracking history."""
    summary = preview()

    jobs.reset()
    recordings.delete_all_recordings()

    _clear_dir(downloads._downloads_dir())
    _clear_dir(youtube.YOUTUBE_DIR)
    _clear_dir(downloads._records_dir())

    for manifest in downloads.list_batch_manifests(limit=1000):
        manifest.unlink(missing_ok=True)
    for log in (db.data_dir() / "logs").glob("series_batch_*.log"):
        log.unlink(missing_ok=True)

    return summary
