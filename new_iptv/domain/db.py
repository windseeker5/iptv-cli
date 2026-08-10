"""SQLite database helpers and migrations."""

import os
import sqlite3
from pathlib import Path


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def data_dir() -> Path:
    """Return the absolute path to the data directory, creating it if needed."""
    path = Path(DEFAULT_DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    """Return the absolute path to the SQLite database file."""
    return data_dir() / "iptv.db"


def get_connection() -> sqlite3.Connection:
    """Open a connection to the IPTV database."""
    return sqlite3.connect(str(db_path()))


def init_db() -> None:
    """Create base tables and indexes used by the application."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS live_streams (
                stream_id INTEGER PRIMARY KEY,
                name TEXT,
                category_id INTEGER,
                stream_url TEXT,
                category_name TEXT,
                epg_channel_id TEXT
            );

            CREATE TABLE IF NOT EXISTS vod_streams (
                stream_id INTEGER PRIMARY KEY,
                name TEXT,
                category_id INTEGER,
                stream_url TEXT,
                year TEXT,
                rating REAL,
                genre TEXT
            );

            CREATE TABLE IF NOT EXISTS vod_categories (
                category_id INTEGER PRIMARY KEY,
                category_name TEXT,
                parent_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS account_info (
                username TEXT,
                status TEXT,
                exp_date INTEGER,
                max_connections TEXT
            );

            CREATE TABLE IF NOT EXISTS epg (
                stream_id INTEGER,
                start_time INTEGER,
                end_time INTEGER,
                title TEXT,
                description TEXT,
                cached_at INTEGER,
                PRIMARY KEY (stream_id, start_time)
            );

            CREATE TABLE IF NOT EXISTS series_streams (
                series_id INTEGER PRIMARY KEY,
                name TEXT,
                category_id INTEGER,
                cover TEXT,
                plot TEXT,
                cast TEXT,
                genre TEXT,
                rating REAL,
                category_name TEXT
            );

            CREATE TABLE IF NOT EXISTS series_episodes (
                episode_id TEXT PRIMARY KEY,
                series_id INTEGER,
                season_number INTEGER,
                season_name TEXT,
                episode_num INTEGER,
                title TEXT,
                container_extension TEXT,
                stream_url TEXT,
                air_date TEXT,
                duration TEXT,
                duration_secs INTEGER,
                rating REAL,
                added TEXT,
                direct_source TEXT,
                last_cached_at INTEGER
            );

            CREATE TABLE IF NOT EXISTS scheduled_recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stream_id INTEGER,
                channel_name TEXT,
                start_time INTEGER,
                duration INTEGER,
                output_path TEXT,
                timer_unit TEXT,
                status TEXT DEFAULT 'pending',
                created_at INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_live_name ON live_streams(name);
            CREATE INDEX IF NOT EXISTS idx_vod_name ON vod_streams(name);
            CREATE INDEX IF NOT EXISTS idx_vod_cat_name ON vod_categories(category_name);
            CREATE INDEX IF NOT EXISTS idx_epg_stream ON epg(stream_id);
            CREATE INDEX IF NOT EXISTS idx_epg_time ON epg(start_time, end_time);
            CREATE INDEX IF NOT EXISTS idx_series_name ON series_streams(name);
            CREATE INDEX IF NOT EXISTS idx_series_episodes_series
                ON series_episodes(series_id, season_number, episode_num);
            CREATE INDEX IF NOT EXISTS idx_series_episode_lookup
                ON series_episodes(series_id, season_number, episode_num);
            """
        )
        conn.commit()


def row_count(table: str) -> int:
    """Return the number of rows in a table."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        return cursor.fetchone()[0]


def list_tables() -> list[str]:
    """Return a list of user tables in the database."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return [row[0] for row in cursor.fetchall()]


def table_counts() -> dict[str, int]:
    """Return row counts for all user tables."""
    counts = {}
    for table in list_tables():
        counts[table] = row_count(table)
    return counts
