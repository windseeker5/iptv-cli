"""IPTV provider data access layer."""

import base64
import re
import sqlite3
import time
from urllib.parse import urlparse

import requests

from new_iptv.domain import config, db


def _decode_base64_if_needed(text):
    """Decode base64 text if it appears to be encoded."""
    if not text:
        return None
    try:
        if all(
            c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
            for c in text.strip()
        ):
            return base64.b64decode(text).decode("utf-8", errors="ignore")
    except Exception:
        pass
    return text


def build_stream_url(stream_id: int) -> str:
    """Build a direct stream URL from a stream ID."""
    return f"{config.Config.IPTV_SERVER_URL}/live/{config.Config.IPTV_USERNAME}/{config.Config.IPTV_PASSWORD}/{stream_id}.ts"


def build_series_url(stream_id: int, container_extension: str = "mp4") -> str:
    """Build a series episode stream URL."""
    return f"{config.Config.IPTV_SERVER_URL}/series/{config.Config.IPTV_USERNAME}/{config.Config.IPTV_PASSWORD}/{stream_id}.{container_extension}"


def _search_table(query: str, table: str, columns: list[str], limit: int = 50):
    """Generic tokenized search against a table."""
    tokens = query.split()
    if not tokens:
        return []

    conditions = " AND ".join(["name LIKE ?" for _ in tokens])
    params = [f"%{token}%" for token in tokens]

    sql = f"""
        SELECT {', '.join(columns)}
        FROM {table}
        WHERE {conditions}
        ORDER BY name
        LIMIT ?
    """
    params.append(limit)

    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def search_live_channels(query: str, limit: int = 50) -> list[dict]:
    """Search live channels by name."""
    columns = [
        "name",
        "category_name",
        "stream_id",
        "stream_url",
        "epg_channel_id",
    ]
    return _search_table(query, "live_streams", columns, limit)


def search_vod_content(query: str, limit: int = 50) -> list[dict]:
    """Search VOD content by name."""
    columns = [
        "stream_id",
        "name",
        "category_id",
        "stream_url",
        "year",
        "rating",
        "genre",
        "category_name",
    ]
    return _search_table(query, "vod_streams", columns, limit)


def search_series_content(query: str, limit: int = 50) -> list[dict]:
    """Search series by name."""
    columns = [
        "series_id",
        "name",
        "category_id",
        "cover",
        "plot",
        "cast",
        "genre",
        "rating",
        "category_name",
    ]
    return _search_table(query, "series_streams", columns, limit)


def get_live_categories() -> list[dict]:
    """Return distinct live categories."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT DISTINCT category_name
            FROM live_streams
            WHERE category_name IS NOT NULL AND category_name != ''
            ORDER BY category_name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_vod_categories() -> list[dict]:
    """Return VOD categories."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT category_id, category_name, parent_id
            FROM vod_categories
            ORDER BY category_name
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_channels_by_category(category_name: str, limit: int = 200) -> list[dict]:
    """Return live channels for a given category."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT name, category_name, stream_id, stream_url, epg_channel_id
            FROM live_streams
            WHERE category_name = ?
            ORDER BY name
            LIMIT ?
            """,
            (category_name, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_vod_by_category(category_name: str, limit: int = 200) -> list[dict]:
    """Return VOD items for a given category."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT stream_id, name, category_id, stream_url, year, rating, genre, category_name
            FROM vod_streams
            WHERE category_name = ?
            ORDER BY name
            LIMIT ?
            """,
            (category_name, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_series_info(series_id: int) -> dict | None:
    """Return series metadata."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT * FROM series_streams WHERE series_id = ?", (series_id,)
        ).fetchone()
        return dict(row) if row else None


def get_series_episodes(series_id: int) -> list[dict]:
    """Return episodes for a series, ordered by season and episode number."""
    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute(
            """
            SELECT *
            FROM series_episodes
            WHERE series_id = ?
            ORDER BY season_number, episode_num
            """,
            (series_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_epg_candidates() -> list[str]:
    """Return candidate server bases for EPG fetching."""
    candidates = []
    if config.Config.EPG_SERVER_URL:
        candidates.append(config.Config.EPG_SERVER_URL.rstrip("/"))
    candidates.append(config.Config.IPTV_SERVER_URL.rstrip("/"))
    return list(dict.fromkeys(candidates))


def fetch_epg_listings(
    stream_id: int, channel_name: str | None = None, limit: int = 2
) -> list[dict]:
    """Fetch EPG listings from the provider API."""
    candidates = get_epg_candidates()
    errors = []

    def try_fetch(param_value, server_base):
        host = urlparse(server_base).netloc or server_base
        try:
            url = f"{server_base}/player_api.php"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            params = {
                "username": config.Config.IPTV_USERNAME,
                "password": config.Config.IPTV_PASSWORD,
                "action": "get_short_epg",
                "stream_id": param_value,
                "limit": limit,
            }
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                listings = data.get("epg_listings", []) if isinstance(data, dict) else []
                if listings:
                    return listings
                errors.append(f"{host}: empty EPG")
            else:
                errors.append(f"{host}: HTTP {response.status_code}")
        except requests.exceptions.RequestException as exc:
            errors.append(f"{host}: {exc.__class__.__name__}")
        except Exception as exc:
            errors.append(f"{host}: {exc.__class__.__name__}")
        return []

    # Strategy 1: direct stream_id
    for server_base in candidates:
        listings = try_fetch(stream_id, server_base)
        if listings:
            return listings

    if not channel_name:
        return []

    # Strategy 2: strip quality suffixes
    base_name = channel_name
    for suffix in [
        " HD",
        " FHD",
        " SD",
        " 4K",
        " UHD",
        " ᴴᴰ",
        " (HD)",
        " [HD]",
    ]:
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)].strip()
            break

    base_name_no_number = re.sub(r"\s+\d+$", "", base_name).strip()

    if base_name != channel_name:
        for server_base in candidates:
            listings = try_fetch(base_name, server_base)
            if listings:
                return listings

    if base_name_no_number != base_name:
        for server_base in candidates:
            listings = try_fetch(base_name_no_number, server_base)
            if listings:
                return listings

    # Strategy 3: similar channels in database
    try:
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT stream_id, name
                FROM live_streams
                WHERE name LIKE ?
                ORDER BY
                    CASE WHEN name = ? THEN 0
                         WHEN name LIKE ? THEN 1
                         ELSE 2
                    END
                LIMIT 10
                """,
                (f"%{base_name_no_number}%", base_name, f"{base_name}%"),
            )
            similar_channels = cursor.fetchall()

        for similar_id, similar_name in similar_channels:
            if similar_id == stream_id:
                continue
            for server_base in candidates:
                listings = try_fetch(similar_id, server_base)
                if listings:
                    return listings
    except Exception:
        pass

    return []


def cache_epg_listings(stream_id: int, listings: list[dict]) -> None:
    """Insert EPG listings into the local cache."""
    now = int(time.time())
    rows = []
    for item in listings:
        try:
            start = int(item.get("start_timestamp", item.get("start", "0")) or 0)
            end = int(item.get("stop_timestamp", item.get("stop", "0")) or 0)
            if start == 0 or end == 0:
                continue
            title = _decode_base64_if_needed(item.get("title", "")) or ""
            description = _decode_base64_if_needed(item.get("description", "")) or ""
            rows.append((stream_id, start, end, title, description, now))
        except (ValueError, TypeError):
            continue

    if not rows:
        return

    with db.connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO epg
            (stream_id, start_time, end_time, title, description, cached_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def get_now_playing_local(stream_id: int) -> dict | None:
    """Return currently playing program from local cache if still valid."""
    now = int(time.time())
    cache_max_age = 6 * 3600

    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        row = cursor.execute(
            """
            SELECT title, description, cached_at FROM epg
            WHERE stream_id = ? AND start_time <= ? AND end_time > ?
            ORDER BY start_time DESC
            LIMIT 1
            """,
            (stream_id, now, now),
        ).fetchone()

        if row and (now - (row["cached_at"] or 0)) < cache_max_age:
            return {
                "title": row["title"] if row["title"] else None,
                "description": row["description"] if row["description"] else None,
            }
    return None


def get_now_playing(stream_id: int, channel_name: str | None = None) -> dict | None:
    """Return currently playing program, fetching from network if not cached."""
    cached = get_now_playing_local(stream_id)
    if cached:
        return cached

    listings = fetch_epg_listings(stream_id, channel_name=channel_name, limit=1)
    if listings:
        cache_epg_listings(stream_id, listings)
        program = listings[0]
        return {
            "title": _decode_base64_if_needed(program.get("title", "")) or None,
            "description": _decode_base64_if_needed(program.get("description", ""))
            or None,
        }
    return None


def get_epg_with_upcoming(stream_id: int, channel_name: str | None = None) -> dict:
    """Return now playing and upcoming program for a channel."""
    listings = fetch_epg_listings(stream_id, channel_name=channel_name, limit=2)
    if listings:
        cache_epg_listings(stream_id, listings)

    now = int(time.time())
    result = {"now": None, "next": None}

    with db.connection() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT title, description, start_time, end_time FROM epg
            WHERE stream_id = ? AND start_time <= ? AND end_time > ?
            ORDER BY start_time DESC
            LIMIT 1
            """,
            (stream_id, now, now),
        )
        now_row = cursor.fetchone()
        if now_row:
            result["now"] = dict(now_row)

        cursor.execute(
            """
            SELECT title, description, start_time, end_time FROM epg
            WHERE stream_id = ? AND start_time > ?
            ORDER BY start_time ASC
            LIMIT 1
            """,
            (stream_id, now),
        )
        next_row = cursor.fetchone()
        if next_row:
            result["next"] = dict(next_row)

    return result
