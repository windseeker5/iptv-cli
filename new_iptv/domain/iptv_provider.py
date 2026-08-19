"""IPTV provider data access layer."""

import base64
import io
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
            SELECT v.stream_id, v.name, v.category_id, v.stream_url,
                   v.year, v.rating, v.genre, c.category_name
            FROM vod_streams v
            JOIN vod_categories c ON v.category_id = c.category_id
            WHERE c.category_name = ?
            ORDER BY v.name
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
    """Return episodes for a series, fetching from the API if not cached locally."""
    episodes = _series_episodes_from_db(series_id)
    if episodes:
        return episodes
    fetch_series_episodes(series_id)
    return _series_episodes_from_db(series_id)


def _series_episodes_from_db(series_id: int) -> list[dict]:
    """Read cached series episodes from the database."""
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


def fetch_series_episodes(series_id: int) -> dict:
    """Download and cache episodes for a series. Returns status dict."""
    server = config.Config.IPTV_SERVER_URL.rstrip("/")
    username = config.Config.IPTV_USERNAME
    password = config.Config.IPTV_PASSWORD

    if not server or not username or not password:
        return {"success": False, "message": "Missing IPTV credentials"}

    session = _epg_session()
    try:
        session.get(server, timeout=5)
    except Exception:
        pass

    try:
        url = f"{server}/player_api.php"
        params = {
            "username": username,
            "password": password,
            "action": "get_series_info",
            "series_id": series_id,
        }
        response = session.get(url, params=params, timeout=60)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return {"success": False, "message": f"Failed to fetch series info: {exc}"}

    episodes_data = data.get("episodes", {}) if isinstance(data, dict) else {}
    if not episodes_data:
        return {"success": False, "message": "No episodes in provider response"}

    now = int(time.time())
    rows = []
    for season_key, episodes in episodes_data.items():
        if not isinstance(episodes, list):
            continue
        for ep in episodes:
            ep_id = ep.get("id")
            info = ep.get("info", {}) or {}
            container = ep.get("container_extension") or "mp4"
            stream_url = (
                f"{server}/series/{username}/{password}/{ep_id}.{container}"
                if ep_id
                else ""
            )
            rows.append(
                (
                    str(ep_id),
                    series_id,
                    ep.get("season"),
                    info.get("season_name", ""),
                    ep.get("episode_num"),
                    ep.get("title", ""),
                    container,
                    stream_url,
                    info.get("air_date", ""),
                    info.get("duration", ""),
                    info.get("duration_secs", 0),
                    info.get("rating"),
                    ep.get("added", ""),
                    ep.get("direct_source", ""),
                    now,
                )
            )

    if not rows:
        return {"success": False, "message": "No episodes to cache"}

    try:
        with db.connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO series_episodes
                (episode_id, series_id, season_number, season_name, episode_num, title,
                 container_extension, stream_url, air_date, duration, duration_secs,
                 rating, added, direct_source, last_cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            conn.commit()
    except Exception as exc:
        return {"success": False, "message": f"Failed to cache episodes: {exc}"}

    return {"success": True, "message": f"Cached {len(rows)} episodes", "count": len(rows)}


def _epg_session() -> requests.Session:
    """Return a requests Session configured like a browser.

    Some providers sit behind Cloudflare and reject cold API calls; a Session
    with a realistic UA is enough to get 200 responses.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/html, application/xhtml+xml, application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def _parse_xmltv_time(text: str) -> int:
    """Parse an XMLTV timestamp like '20250811180000 +0000' into epoch seconds."""
    text = (text or "").strip()
    if not text:
        return 0
    # XMLTV can include a timezone offset; handle both forms.
    if len(text) >= 19 and text[14] == " " and text[15] in "+-":
        dt = datetime.strptime(text, "%Y%m%d%H%M%S %z")
    else:
        dt = datetime.strptime(text[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def epg_last_update_time() -> int:
    """Return the latest cached_at timestamp from the epg table, or 0."""
    try:
        with db.connection() as conn:
            cursor = conn.cursor()
            row = cursor.execute("SELECT MAX(cached_at) FROM epg").fetchone()
            return row[0] or 0
    except Exception:
        return 0


def download_full_epg(force: bool = False) -> dict:
    """Download the provider's XMLTV EPG and populate the local cache.

    Returns a dict with success, message, and channels_updated.
    """
    now = int(time.time())
    max_age = 4 * 3600  # refresh every 4 hours
    if not force and (now - epg_last_update_time()) < max_age:
        return {"success": True, "message": "EPG cache is recent", "channels_updated": 0}

    server = config.Config.IPTV_SERVER_URL.rstrip("/")
    if not server:
        return {"success": False, "message": "No IPTV server configured", "channels_updated": 0}

    session = _epg_session()
    # Warm-up request helps avoid Cloudflare challenges on some providers.
    try:
        session.get(server, timeout=5)
    except Exception:
        pass

    url = f"{server}/xmltv.php"
    params = {
        "username": config.Config.IPTV_USERNAME,
        "password": config.Config.IPTV_PASSWORD,
    }

    try:
        response = session.get(url, params=params, timeout=120)
        if response.status_code != 200:
            return {
                "success": False,
                "message": f"XMLTV returned HTTP {response.status_code}",
                "channels_updated": 0,
            }
    except Exception as exc:
        return {"success": False, "message": f"XMLTV download failed: {exc}", "channels_updated": 0}

    # Build map of XMLTV channel id -> stream_ids from our database.
    try:
        with db.connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute(
                """
                SELECT stream_id, epg_channel_id
                FROM live_streams
                WHERE epg_channel_id IS NOT NULL AND epg_channel_id != ''
                """
            ).fetchall()
        channel_to_streams: dict[str, list[int]] = {}
        for row in rows:
            channel_to_streams.setdefault(row["epg_channel_id"], []).append(
                row["stream_id"]
            )
    except Exception as exc:
        return {
            "success": False,
            "message": f"Failed to read channel map: {exc}",
            "channels_updated": 0,
        }

    if not channel_to_streams:
        return {
            "success": False,
            "message": "No epg_channel_id mappings in database",
            "channels_updated": 0,
        }

    rows: list[tuple] = []
    channels_with_data: set[str] = set()
    channels_updated: set[int] = set()

    try:
        context = ET.iterparse(io.BytesIO(response.content), events=("end",))
        for event, elem in context:
            if elem.tag == "programme":
                channel_id = elem.get("channel")
                if channel_id and channel_id in channel_to_streams:
                    start = _parse_xmltv_time(elem.get("start", ""))
                    stop = _parse_xmltv_time(elem.get("stop", ""))
                    if start and stop:
                        title = ""
                        desc = ""
                        for child in elem:
                            if child.tag == "title":
                                title = child.text or ""
                            elif child.tag == "desc":
                                desc = child.text or ""
                        for stream_id in channel_to_streams[channel_id]:
                            rows.append((stream_id, start, stop, title, desc, now))
                            channels_updated.add(stream_id)
                        channels_with_data.add(channel_id)
                elem.clear()
            elif elem.tag == "channel":
                elem.clear()
    except Exception as exc:
        return {
            "success": False,
            "message": f"XMLTV parse failed: {exc}",
            "channels_updated": len(channels_updated),
        }

    if not rows:
        return {
            "success": False,
            "message": "XMLTV contained no programmes for known channels",
            "channels_updated": 0,
        }

    try:
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
            # Keep the cache tidy: remove programmes that already ended.
            cursor.execute("DELETE FROM epg WHERE end_time < ?", (now - 3600,))
            conn.commit()
    except Exception as exc:
        return {
            "success": False,
            "message": f"Failed to write EPG cache: {exc}",
            "channels_updated": len(channels_updated),
        }

    return {
        "success": True,
        "message": f"EPG updated: {len(channels_updated)} channels, {len(rows)} programmes",
        "channels_updated": len(channels_updated),
    }


def download_database(
    components: list[str] | None = None,
    save_json: bool = True,
) -> dict:
    """Download live/VOD/series data from the provider and refresh the local DB.

    Components default to the full set used by the app.
    Returns {"success": bool, "message": str}.
    """
    if components is None:
        components = [
            "account_info",
            "live_categories",
            "live_streams",
            "vod_categories",
            "vod_streams",
            "series_categories",
            "series_streams",
        ]

    server = config.Config.IPTV_SERVER_URL.rstrip("/")
    username = config.Config.IPTV_USERNAME
    password = config.Config.IPTV_PASSWORD

    if not server or not username or not password:
        return {"success": False, "message": "Missing IPTV credentials"}

    session = _epg_session()
    # Warm-up to avoid Cloudflare challenges on some providers.
    try:
        session.get(server, timeout=5)
    except Exception:
        pass

    data: dict[str, list | dict] = {}

    def fetch_component(action: str | None) -> list | dict:
        url = f"{server}/player_api.php"
        params: dict[str, str] = {"username": username, "password": password}
        if action:
            params["action"] = action
        response = session.get(url, params=params, timeout=120)
        response.raise_for_status()
        return response.json()

    try:
        for component in components:
            action = None if component == "account_info" else f"get_{component}"
            if component == "series_streams":
                action = "get_series"
            data[component] = fetch_component(action)
            if save_json:
                cache_dir = db.data_dir() / "cache"
                cache_dir.mkdir(parents=True, exist_ok=True)
                path = cache_dir / f"{component}.json"
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data[component], f, indent=2)
    except Exception as exc:
        return {"success": False, "message": f"Download failed ({component}): {exc}"}

    # Build lookup maps.
    live_categories: dict[str | int, str] = {}
    for cat in data.get("live_categories", []) or []:
        live_categories[cat.get("category_id")] = cat.get("category_name", "Unknown")

    vod_categories: dict[str | int, str] = {}
    for cat in data.get("vod_categories", []) or []:
        vod_categories[cat.get("category_id")] = cat.get("category_name", "Unknown")

    series_categories: dict[str | int, str] = {}
    for cat in data.get("series_categories", []) or []:
        series_categories[cat.get("category_id")] = cat.get(
            "category_name", "Unknown"
        )

    db.init_db()

    try:
        with db.connection() as conn:
            cursor = conn.cursor()

            # Clear existing data for refreshed components.
            if "account_info" in components:
                cursor.execute("DELETE FROM account_info")
            if "live_streams" in components:
                cursor.execute("DELETE FROM live_streams")
            if "vod_categories" in components:
                cursor.execute("DELETE FROM vod_categories")
            if "vod_streams" in components:
                cursor.execute("DELETE FROM vod_streams")
            if "series_streams" in components:
                cursor.execute("DELETE FROM series_streams")
                cursor.execute("DELETE FROM series_episodes")

            # Account info.
            if "account_info" in components:
                user_info = (
                    data.get("account_info", {}).get("user_info", {})
                    if isinstance(data.get("account_info"), dict)
                    else {}
                )
                cursor.execute(
                    "INSERT INTO account_info VALUES (?, ?, ?, ?)",
                    (
                        user_info.get("username"),
                        user_info.get("status"),
                        user_info.get("exp_date"),
                        user_info.get("max_connections"),
                    ),
                )

            # Live streams.
            if "live_streams" in components:
                for stream in data.get("live_streams", []) or []:
                    stream_id = stream.get("stream_id")
                    cursor.execute(
                        """
                        INSERT INTO live_streams
                        (stream_id, name, category_id, stream_url, category_name, epg_channel_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            stream_id,
                            stream.get("name"),
                            stream.get("category_id"),
                            f"{server}/live/{username}/{password}/{stream_id}.ts",
                            live_categories.get(
                                stream.get("category_id"), "Unknown"
                            ),
                            stream.get("epg_channel_id", ""),
                        ),
                    )

            # VOD categories.
            if "vod_categories" in components:
                for cat in data.get("vod_categories", []) or []:
                    cursor.execute(
                        "INSERT INTO vod_categories VALUES (?, ?, ?)",
                        (
                            cat.get("category_id"),
                            cat.get("category_name"),
                            cat.get("parent_id"),
                        ),
                    )

            # VOD streams.
            if "vod_streams" in components:
                for stream in data.get("vod_streams", []) or []:
                    stream_id = stream.get("stream_id")
                    ext = stream.get("container_extension") or "mp4"
                    cursor.execute(
                        """
                        INSERT INTO vod_streams
                        (stream_id, name, category_id, stream_url, year, rating, genre)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            stream_id,
                            stream.get("name"),
                            stream.get("category_id"),
                            f"{server}/movie/{username}/{password}/{stream_id}.{ext}",
                            stream.get("year"),
                            stream.get("rating"),
                            stream.get("genre"),
                        ),
                    )

            # Series streams.
            if "series_streams" in components:
                for show in data.get("series_streams", []) or []:
                    cat_id = show.get("category_id")
                    cursor.execute(
                        """
                        INSERT INTO series_streams
                        (series_id, name, category_id, cover, plot, cast, genre, rating, category_name)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            show.get("series_id"),
                            show.get("name"),
                            cat_id,
                            show.get("cover"),
                            show.get("plot"),
                            show.get("cast"),
                            show.get("genre"),
                            show.get("rating"),
                            series_categories.get(cat_id, "Unknown"),
                        ),
                    )

            conn.commit()
    except Exception as exc:
        return {"success": False, "message": f"Database write failed: {exc}"}

    counts = {
        "live": len(data.get("live_streams", []) or []),
        "vod": len(data.get("vod_streams", []) or []),
        "series": len(data.get("series_streams", []) or []),
    }
    return {
        "success": True,
        "message": f"Database updated: {counts['live']:,} live / {counts['vod']:,} VOD / {counts['series']:,} series",
        "counts": counts,
    }


def get_epg_candidates(stream_url: str | None = None) -> list[str]:
    """Return candidate API bases for EPG fetching, including stream URL domain."""

    def _add(base_url: str, candidates: list[str]) -> None:
        normalized = str(base_url).strip().rstrip("/")
        if normalized and normalized not in candidates:
            candidates.append(normalized)

    def _add_with_flip(base_url: str, candidates: list[str]) -> None:
        _add(base_url, candidates)
        if base_url.startswith("http://"):
            _add(base_url.replace("http://", "https://", 1), candidates)
        elif base_url.startswith("https://"):
            _add(base_url.replace("https://", "http://", 1), candidates)

    candidates: list[str] = []
    if config.Config.EPG_SERVER_URL:
        _add_with_flip(config.Config.EPG_SERVER_URL, candidates)
    if config.Config.IPTV_SERVER_URL:
        _add_with_flip(config.Config.IPTV_SERVER_URL, candidates)

    if stream_url:
        parsed = urlparse(str(stream_url))
        if parsed.scheme and parsed.netloc:
            _add_with_flip(f"{parsed.scheme}://{parsed.netloc}", candidates)

    return candidates


def fetch_epg_listings(
    stream_id: int,
    channel_name: str | None = None,
    stream_url: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch EPG listings from the provider API using multiple candidate bases."""
    candidates = get_epg_candidates(stream_url=stream_url)
    errors = []
    session = _epg_session()

    def try_fetch(param_value, server_base):
        host = urlparse(server_base).netloc or server_base
        try:
            url = f"{server_base}/player_api.php"
            params = {
                "username": config.Config.IPTV_USERNAME,
                "password": config.Config.IPTV_PASSWORD,
                "action": "get_short_epg",
                "stream_id": param_value,
                "limit": limit,
            }
            response = session.get(url, params=params, timeout=10)
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

    def try_fetch_simple(param_value, server_base):
        """Fallback using get_simple_data_table; some providers only expose EPG there."""
        host = urlparse(server_base).netloc or server_base
        try:
            url = f"{server_base}/player_api.php"
            params = {
                "username": config.Config.IPTV_USERNAME,
                "password": config.Config.IPTV_PASSWORD,
                "action": "get_simple_data_table",
                "stream_id": param_value,
                "limit": limit,
            }
            response = session.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                raw_listings = data.get("epg_listings", []) if isinstance(data, dict) else []
                if raw_listings:
                    normalized = []
                    for item in raw_listings:
                        title = _decode_base64_if_needed(item.get("title", "")) or ""
                        description = _decode_base64_if_needed(item.get("description", "")) or ""
                        start = item.get("start_timestamp")
                        end = item.get("stop_timestamp")
                        if not start:
                            try:
                                start = int(
                                    datetime.strptime(item.get("start", ""), "%Y-%m-%d %H:%M:%S")
                                    .replace(tzinfo=timezone.utc)
                                    .timestamp()
                                )
                            except Exception:
                                start = 0
                        if not end:
                            try:
                                end = int(
                                    datetime.strptime(item.get("end", ""), "%Y-%m-%d %H:%M:%S")
                                    .replace(tzinfo=timezone.utc)
                                    .timestamp()
                                )
                            except Exception:
                                end = 0
                        if start and end:
                            normalized.append(
                                {
                                    "start_timestamp": start,
                                    "stop_timestamp": end,
                                    "title": title,
                                    "description": description,
                                }
                            )
                    if normalized:
                        return normalized
                errors.append(f"{host}: simple EPG empty")
            else:
                errors.append(f"{host}: simple HTTP {response.status_code}")
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
    similar_channels = []
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
                (f"%{base_name_no_number}%", base_name, f"%{base_name}%"),
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

    # Fallback: some providers only expose EPG via get_simple_data_table.
    for server_base in candidates:
        listings = try_fetch_simple(stream_id, server_base)
        if listings:
            return listings

    if channel_name:
        if base_name != channel_name:
            for server_base in candidates:
                listings = try_fetch_simple(base_name, server_base)
                if listings:
                    return listings
        if base_name_no_number != base_name:
            for server_base in candidates:
                listings = try_fetch_simple(base_name_no_number, server_base)
                if listings:
                    return listings

    for similar_id, similar_name in similar_channels:
        if similar_id == stream_id:
            continue
        for server_base in candidates:
            listings = try_fetch_simple(similar_id, server_base)
            if listings:
                return listings

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


def get_now_playing(
    stream_id: int,
    channel_name: str | None = None,
    stream_url: str | None = None,
) -> dict | None:
    """Return currently playing program, fetching from network if not cached."""
    cached = get_now_playing_local(stream_id)
    if cached:
        return {**cached, "current": True}

    listings = fetch_epg_listings(
        stream_id, channel_name=channel_name, stream_url=stream_url, limit=10
    )
    if not listings:
        return None

    cache_epg_listings(stream_id, listings)

    now = int(time.time())
    for program in listings:
        try:
            start = int(
                program.get("start_timestamp", program.get("start", "0")) or 0
            )
            end = int(program.get("stop_timestamp", program.get("stop", "0")) or 0)
            if start <= now < end:
                return {
                    "title": _decode_base64_if_needed(program.get("title", ""))
                    or None,
                    "description": _decode_base64_if_needed(
                        program.get("description", "")
                    )
                    or None,
                    "current": True,
                    "start": start,
                    "end": end,
                }
        except (ValueError, TypeError):
            continue

    # Fall back to the most recent/upcoming listing if no current window is found.
    program = listings[-1]
    start = int(program.get("start_timestamp", program.get("start", "0")) or 0)
    end = int(program.get("stop_timestamp", program.get("stop", "0")) or 0)
    return {
        "title": _decode_base64_if_needed(program.get("title", "")) or None,
        "description": _decode_base64_if_needed(program.get("description", ""))
        or None,
        "current": False,
        "start": start,
        "end": end,
    }


def get_epg_with_upcoming(
    stream_id: int,
    channel_name: str | None = None,
    stream_url: str | None = None,
) -> dict:
    """Return now playing and upcoming program for a channel."""
    listings = fetch_epg_listings(
        stream_id, channel_name=channel_name, stream_url=stream_url, limit=10
    )
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
