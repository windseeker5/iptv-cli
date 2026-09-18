"""Discover and manage completed media files."""

from pathlib import Path

from iptv_tui.domain import downloads, youtube

MEDIA_EXTENSIONS = {
    ".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".ts", ".webm"
}


def media_roots() -> list[tuple[str, Path]]:
    """Return distinct media locations with user-facing labels."""
    roots = [
        ("Recording", downloads._records_dir()),
        ("Download", downloads._downloads_dir()),
        ("YouTube", youtube.YOUTUBE_DIR),
    ]
    seen = set()
    unique = []
    for label, path in roots:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append((label, path))
    return unique


def list_media() -> list[dict]:
    """Return completed media files, newest first."""
    rows = []
    for kind, root in media_roots():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            stat = path.stat()
            rows.append(
                {
                    "kind": kind,
                    "name": path.name,
                    "path": path,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
    rows.sort(key=lambda row: row["modified"], reverse=True)
    return rows


def delete_media(path: Path) -> bool:
    """Delete a media file only when it belongs to a configured media root."""
    resolved = path.resolve()
    allowed = any(
        resolved.is_relative_to(root.resolve()) for _, root in media_roots()
    )
    if not allowed or not resolved.is_file():
        return False
    resolved.unlink()
    return True
