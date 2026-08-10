"""Favorites persistence and M3U playlist generation."""

import json
import os
from datetime import datetime
from pathlib import Path

from new_iptv.domain import config, db, iptv_provider


def data_dir() -> Path:
    """Return the data directory path."""
    path = db.data_dir()
    return path


def favorites_path() -> Path:
    """Return the path to favorites.json."""
    return data_dir() / "favorites.json"


def seed_path() -> Path:
    """Return the path to favorites_seed.json."""
    return Path(__file__).resolve().parents[2] / "favorites_seed.json"


def load_favorites() -> list[dict]:
    """Load favorites from JSON, migrating from old location if needed."""
    path = favorites_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: error loading favorites: {e}")

    # Fall back to old location
    old_path = Path("favorites.json")
    if old_path.exists():
        try:
            with open(old_path, "r", encoding="utf-8") as f:
                favs = json.load(f)
            data_dir().mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(favs, f, indent=2)
            old_path.unlink()
            return favs
        except Exception as e:
            print(f"Warning: error migrating favorites: {e}")

    return import_favorites_seed()


def save_favorites(favorites: list[dict]) -> None:
    """Persist favorites list to disk."""
    data_dir().mkdir(parents=True, exist_ok=True)
    with open(favorites_path(), "w", encoding="utf-8") as f:
        json.dump(favorites, f, indent=2)


def add_favorite(item: dict, item_type: str = "live") -> int:
    """Add an item to favorites. Returns total count, or -1 if already present."""
    favs = load_favorites()
    stream_id = item.get("stream_id", 0)

    for existing in favs:
        if existing.get("stream_id") == stream_id and existing.get("type") == item_type:
            return -1

    favorite_item = {
        "stream_id": stream_id,
        "name": item.get("name", "Unknown"),
        "stream_url": item.get("stream_url", ""),
        "category": item.get("category_name", "Uncategorized"),
        "type": item_type,
        "added": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    favs.append(favorite_item)
    save_favorites(favs)
    generate_m3u_playlist()
    export_favorites_seed()
    return len(favs)


def remove_favorite(item: dict, item_type: str = "live") -> int:
    """Remove an item from favorites. Returns new count, or -1 if not found."""
    favs = load_favorites()
    stream_id = item.get("stream_id", 0)
    original_count = len(favs)

    favs = [
        f
        for f in favs
        if not (f.get("stream_id") == stream_id and f.get("type") == item_type)
    ]

    if len(favs) < original_count:
        save_favorites(favs)
        generate_m3u_playlist()
        export_favorites_seed()
        return len(favs)

    return -1


def is_favorite(item: dict, item_type: str = "live") -> bool:
    """Check if an item is in favorites."""
    stream_id = item.get("stream_id", 0)
    return any(
        f.get("stream_id") == stream_id and f.get("type") == item_type
        for f in load_favorites()
    )


def get_favorites_set() -> set[tuple[int, str]]:
    """Return favorites as a set for quick lookups."""
    return {(f.get("stream_id", 0), f.get("type")) for f in load_favorites()}


def generate_m3u_playlist() -> bool:
    """Generate M3U playlist from favorites into data/ and nginx/html/."""
    try:
        favs = load_favorites()
        lines = ["#EXTM3U"]
        for fav in favs:
            category = fav.get("category", "Uncategorized")
            name = fav.get("name", "Unknown")
            url = fav.get("stream_url", "")
            lines.append(f'#EXTINF:-1 group-title="{category}",{name}')
            lines.append(url)
        content = "\n".join(lines) + "\n"

        nginx_dir = Path(__file__).resolve().parents[2] / "nginx" / "html"
        nginx_dir.mkdir(parents=True, exist_ok=True)
        with open(nginx_dir / "iptv.m3u", "w", encoding="utf-8") as f:
            f.write(content)

        with open(data_dir() / "iptv.m3u", "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        print(f"Error generating M3U playlist: {e}")
        return False


def export_favorites_seed() -> None:
    """Export credential-free favorites to seed file for git tracking."""
    try:
        favs = load_favorites()
        seed = []
        for fav in favs:
            seed.append(
                {
                    "stream_id": fav.get("stream_id", 0),
                    "name": fav.get("name", "Unknown"),
                    "category": fav.get(
                        "category", fav.get("category_name", "Uncategorized")
                    ),
                    "type": fav.get("type", "live"),
                    "added": fav.get("added", ""),
                }
            )
        with open(seed_path(), "w", encoding="utf-8") as f:
            json.dump(seed, f, indent=2)
    except Exception as e:
        print(f"Warning: error exporting favorites seed: {e}")


def import_favorites_seed() -> list[dict]:
    """Import favorites from seed file on fresh clone."""
    try:
        path = seed_path()
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            seed = json.load(f)

        favs = []
        for item in seed:
            favs.append(
                {
                    "stream_id": item.get("stream_id", 0),
                    "name": item.get("name", "Unknown"),
                    "stream_url": "",
                    "category": item.get("category", "Uncategorized"),
                    "type": item.get("type", "live"),
                    "added": item.get(
                        "added", datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ),
                }
            )
        if favs:
            save_favorites(favs)
            print(f"Imported {len(favs)} favorites from seed file")
        return favs
    except Exception as e:
        print(f"Warning: error importing favorites seed: {e}")
        return []


def hydrate_favorites_with_database(favorites: list[dict]) -> list[dict]:
    """Refresh favorite stream URLs and metadata from the database."""
    refreshed = [dict(item) for item in favorites]
    live_ids = [
        item["stream_id"]
        for item in refreshed
        if item.get("type") == "live" and item.get("stream_id")
    ]
    vod_ids = [
        item["stream_id"]
        for item in refreshed
        if item.get("type") == "vod" and item.get("stream_id")
    ]

    if live_ids:
        with db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(live_ids))
            rows = cursor.execute(
                f"SELECT stream_id, name, category_name, stream_url FROM live_streams WHERE stream_id IN ({placeholders})",
                live_ids,
            ).fetchall()
            live_meta = {row["stream_id"]: dict(row) for row in rows}

        for item in refreshed:
            if item.get("type") != "live":
                continue
            meta = live_meta.get(item.get("stream_id"))
            if meta:
                item["name"] = meta.get("name", item["name"])
                item["category"] = meta.get("category_name", item.get("category"))
                item["stream_url"] = meta.get("stream_url", item.get("stream_url"))

    if vod_ids:
        with db.get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            placeholders = ",".join("?" * len(vod_ids))
            rows = cursor.execute(
                f"SELECT stream_id, name, category_name, stream_url FROM vod_streams WHERE stream_id IN ({placeholders})",
                vod_ids,
            ).fetchall()
            vod_meta = {row["stream_id"]: dict(row) for row in rows}

        for item in refreshed:
            if item.get("type") != "vod":
                continue
            meta = vod_meta.get(item.get("stream_id"))
            if meta:
                item["name"] = meta.get("name", item["name"])
                item["category"] = meta.get("category_name", item.get("category"))
                item["stream_url"] = meta.get("stream_url", item.get("stream_url"))

    return refreshed
