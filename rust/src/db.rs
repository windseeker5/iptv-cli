//! Read-only access to the CLI's SQLite database (`data/iptv.db`).
//!
//! `kdtv refresh` (the timers, JP) writes this file, so everything else opens it
//! read-only and never holds a transaction open.

use anyhow::{Context, Result};
use chrono::{Local, TimeZone};
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde::Serialize;
use std::path::Path;

pub struct Db(Connection);

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Channel {
    pub stream_id: i64,
    pub name: String,
    pub category: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Program {
    pub title: Option<String>,
    pub start: String,
    pub end: String,
    pub description: String,
    #[serde(skip)]
    pub start_ts: i64,
    #[serde(skip)]
    pub end_ts: i64,
}

#[derive(Serialize, Default)]
pub struct Counts {
    pub live: i64,
    pub vod: i64,
    pub series: i64,
    pub guide_entries: i64,
}

#[derive(Serialize)]
pub struct Account {
    pub status: String,
    pub expires: Option<String>,
    pub max_connections: i64,
}

impl Db {
    pub fn open(data_dir: &Path) -> Result<Db> {
        let path = data_dir.join("iptv.db");
        let conn = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_ONLY)
            .with_context(|| format!("opening {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(2))?;
        Ok(Db(conn))
    }

    /// Channels whose name contains every word of `query` (any case), like the Python search.
    pub fn search_channels(&self, query: &str, limit: usize) -> Result<Vec<Channel>> {
        let words: Vec<String> = query.split_whitespace().map(|w| format!("%{w}%")).collect();
        if words.is_empty() {
            return Ok(vec![]);
        }
        let conditions = vec!["name LIKE ?"; words.len()].join(" AND ");
        let sql = format!(
            "SELECT stream_id, name, category_name FROM live_streams WHERE {conditions} ORDER BY name LIMIT {limit}"
        );
        let mut stmt = self.0.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(words.iter()), channel_from_row)?;
        Ok(rows.collect::<Result<_, _>>()?)
    }

    pub fn channel(&self, stream_id: i64) -> Result<Option<Channel>> {
        Ok(self
            .0
            .query_row(
                "SELECT stream_id, name, category_name FROM live_streams WHERE stream_id = ?",
                params![stream_id],
                channel_from_row,
            )
            .optional()?)
    }

    /// The provider's live categories ("CA| GENERAL FR"…); known by name only, like series.
    pub fn live_categories(&self) -> Result<Vec<Category>> {
        let mut stmt = self.0.prepare(
            "SELECT DISTINCT category_name FROM live_streams WHERE category_name IS NOT NULL ORDER BY category_name",
        )?;
        Ok(stmt.query_map([], |r| Ok(Category { id: None, name: r.get(0)? }))?.collect::<Result<_, _>>()?)
    }

    pub fn channels_in(&self, category: &str, limit: usize) -> Result<Vec<Channel>> {
        let mut stmt = self.0.prepare(&format!(
            "SELECT stream_id, name, category_name FROM live_streams WHERE category_name = ? ORDER BY name LIMIT {limit}"
        ))?;
        Ok(stmt.query_map(params![category], channel_from_row)?.collect::<Result<_, _>>()?)
    }

    /// What's on now and the next few shows. Back-to-back guide blocks with the same
    /// title are merged, since broadcasters often split one long show into several.
    pub fn guide(&self, stream_id: i64, now: i64, count: usize) -> Result<Vec<Program>> {
        let mut stmt = self.0.prepare(
            "SELECT title, description, start_time, end_time FROM epg
             WHERE stream_id = ? AND end_time > ? ORDER BY start_time LIMIT 60",
        )?;
        let rows = stmt.query_map(params![stream_id, now], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, i64>(3)?,
            ))
        })?;
        let mut programs: Vec<Program> = vec![];
        for row in rows {
            let (title, description, start, end) = row?;
            let title = title.filter(|t| !t.is_empty());
            let program = Program {
                start: short_time(start),
                end: short_time(end),
                description: truncate(&description.unwrap_or_default(), 200),
                start_ts: start,
                end_ts: end,
                title,
            };
            if let Some(last) = programs.last_mut() {
                if last.title == program.title && last.end_ts == start {
                    last.end_ts = end;
                    last.end = program.end;
                    continue;
                }
                // The provider sometimes lists overlapping copies of a show. Like the
                // Python guide, the one that started most recently is what's on now…
                if start <= now && last.start_ts <= now {
                    *last = program;
                    continue;
                }
                // …and a later entry that overlaps the previous one is a duplicate.
                if start < last.end_ts {
                    continue;
                }
            }
            if programs.len() == count {
                break;
            }
            programs.push(program);
        }
        Ok(programs)
    }

    pub fn counts(&self) -> Result<Counts> {
        let count = |table: &str| -> Result<i64> {
            Ok(self.0.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))?)
        };
        Ok(Counts {
            live: count("live_streams")?,
            vod: count("vod_streams")?,
            series: count("series_streams")?,
            guide_entries: count("epg")?,
        })
    }

    pub fn account(&self) -> Result<Option<Account>> {
        Ok(self
            .0
            .query_row("SELECT status, exp_date, max_connections FROM account_info LIMIT 1", [], |r| {
                let exp: Option<i64> = r.get(1)?;
                let max: Option<String> = r.get(2)?;
                Ok(Account {
                    status: r.get::<_, Option<String>>(0)?.unwrap_or_default(),
                    expires: exp.and_then(|ts| Local.timestamp_opt(ts, 0).single()).map(|t| t.format("%Y-%m-%d").to_string()),
                    max_connections: max.and_then(|m| m.parse().ok()).unwrap_or(1),
                })
            })
            .optional()?)
    }
}

#[derive(Serialize, Clone, Debug)]
pub struct Category {
    /// Movie categories have a numeric id; live and series categories are known by name only.
    pub id: Option<i64>,
    pub name: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct Movie {
    pub stream_id: i64,
    pub name: String,
    pub year: Option<String>,
    pub rating: Option<f64>,
    pub genre: Option<String>,
    #[serde(skip)]
    extension: String,
}

impl Movie {
    pub fn extension(&self) -> &str {
        &self.extension
    }
}

#[derive(Serialize, Clone, Debug)]
pub struct Series {
    pub series_id: i64,
    pub name: String,
    pub plot: Option<String>,
    pub cast: Option<String>,
    pub genre: Option<String>,
    pub rating: Option<f64>,
    pub category: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
pub struct Episode {
    pub episode_id: String,
    pub series_id: i64,
    pub season: i64,
    pub episode_num: i64,
    pub title: String,
    pub extension: String,
    pub air_date: Option<String>,
    pub duration: Option<String>,
}

const MOVIE_COLUMNS: &str = "stream_id, name, year, rating, genre, stream_url";
const SERIES_COLUMNS: &str = "series_id, name, plot, \"cast\", genre, rating, category_name";
const EPISODE_COLUMNS: &str = "episode_id, series_id, season_number, episode_num, title, container_extension, air_date, duration";

impl Db {
    /// Read-write, for the one thing kd caches itself: series episode lists.
    pub fn open_rw(data_dir: &Path) -> Result<Db> {
        let path = data_dir.join("iptv.db");
        let conn = Connection::open_with_flags(&path, OpenFlags::SQLITE_OPEN_READ_WRITE)
            .with_context(|| format!("opening {}", path.display()))?;
        conn.busy_timeout(std::time::Duration::from_secs(10))?;
        Ok(Db(conn))
    }

    fn search(&self, table: &str, columns: &str, query: &str, limit: usize) -> Result<(String, Vec<String>)> {
        let words: Vec<String> = query.split_whitespace().map(|w| format!("%{w}%")).collect();
        let conditions = vec!["name LIKE ?"; words.len().max(1)].join(" AND ");
        Ok((format!("SELECT {columns} FROM {table} WHERE {conditions} ORDER BY name LIMIT {limit}"), words))
    }

    pub fn search_movies(&self, query: &str, limit: usize) -> Result<Vec<Movie>> {
        if query.trim().is_empty() {
            return Ok(vec![]);
        }
        let (sql, words) = self.search("vod_streams", MOVIE_COLUMNS, query, limit)?;
        let mut stmt = self.0.prepare(&sql)?;
        Ok(stmt.query_map(rusqlite::params_from_iter(words.iter()), movie_from_row)?.collect::<Result<_, _>>()?)
    }

    pub fn movie_categories(&self) -> Result<Vec<Category>> {
        let mut stmt = self.0.prepare(
            "SELECT c.category_id, c.category_name FROM vod_categories c
             WHERE EXISTS (SELECT 1 FROM vod_streams v WHERE v.category_id = c.category_id) ORDER BY c.category_name",
        )?;
        Ok(stmt.query_map([], |r| Ok(Category { id: r.get(0)?, name: r.get::<_, Option<String>>(1)?.unwrap_or_default() }))?
            .collect::<Result<_, _>>()?)
    }

    pub fn movies_in(&self, category_id: i64, limit: usize) -> Result<Vec<Movie>> {
        let mut stmt = self.0.prepare(&format!(
            "SELECT {MOVIE_COLUMNS} FROM vod_streams WHERE category_id = ? ORDER BY name LIMIT {limit}"
        ))?;
        Ok(stmt.query_map(params![category_id], movie_from_row)?.collect::<Result<_, _>>()?)
    }

    pub fn movie(&self, stream_id: i64) -> Result<Option<Movie>> {
        Ok(self
            .0
            .query_row(&format!("SELECT {MOVIE_COLUMNS} FROM vod_streams WHERE stream_id = ?"), params![stream_id], movie_from_row)
            .optional()?)
    }

    pub fn movie_url(&self, stream_id: i64) -> Result<Option<String>> {
        Ok(self
            .0
            .query_row("SELECT stream_url FROM vod_streams WHERE stream_id = ?", params![stream_id], |r| r.get(0))
            .optional()?)
    }

    pub fn search_series(&self, query: &str, limit: usize) -> Result<Vec<Series>> {
        if query.trim().is_empty() {
            return Ok(vec![]);
        }
        let (sql, words) = self.search("series_streams", SERIES_COLUMNS, query, limit)?;
        let mut stmt = self.0.prepare(&sql)?;
        Ok(stmt.query_map(rusqlite::params_from_iter(words.iter()), series_from_row)?.collect::<Result<_, _>>()?)
    }

    pub fn series_categories(&self) -> Result<Vec<Category>> {
        let mut stmt = self.0.prepare(
            "SELECT DISTINCT category_name FROM series_streams WHERE category_name IS NOT NULL ORDER BY category_name",
        )?;
        Ok(stmt.query_map([], |r| Ok(Category { id: None, name: r.get(0)? }))?.collect::<Result<_, _>>()?)
    }

    pub fn series_in(&self, category: &str, limit: usize) -> Result<Vec<Series>> {
        let mut stmt = self.0.prepare(&format!(
            "SELECT {SERIES_COLUMNS} FROM series_streams WHERE category_name = ? ORDER BY name LIMIT {limit}"
        ))?;
        Ok(stmt.query_map(params![category], series_from_row)?.collect::<Result<_, _>>()?)
    }

    pub fn series(&self, series_id: i64) -> Result<Option<Series>> {
        Ok(self
            .0
            .query_row(&format!("SELECT {SERIES_COLUMNS} FROM series_streams WHERE series_id = ?"), params![series_id], series_from_row)
            .optional()?)
    }

    /// Episodes already cached for a series (see `provider::fetch_episodes`).
    pub fn episodes(&self, series_id: i64) -> Result<Vec<Episode>> {
        let mut stmt = self.0.prepare(&format!(
            "SELECT {EPISODE_COLUMNS} FROM series_episodes WHERE series_id = ? ORDER BY season_number, episode_num"
        ))?;
        Ok(stmt.query_map(params![series_id], episode_from_row)?.collect::<Result<_, _>>()?)
    }

    pub fn episode(&self, episode_id: &str) -> Result<Option<Episode>> {
        Ok(self
            .0
            .query_row(&format!("SELECT {EPISODE_COLUMNS} FROM series_episodes WHERE episode_id = ?"), params![episode_id], episode_from_row)
            .optional()?)
    }

    pub fn episode_url(&self, episode_id: &str) -> Result<Option<String>> {
        Ok(self
            .0
            .query_row("SELECT stream_url FROM series_episodes WHERE episode_id = ?", params![episode_id], |r| r.get(0))
            .optional()?)
    }

    /// Save a series' episodes, the same rows `iptv_provider.fetch_series_episodes` writes.
    pub fn cache_episodes(&mut self, rows: &[EpisodeRow]) -> Result<()> {
        let tx = self.0.transaction()?;
        {
            let mut stmt = tx.prepare(
                "INSERT OR REPLACE INTO series_episodes
                 (episode_id, series_id, season_number, season_name, episode_num, title, container_extension,
                  stream_url, air_date, duration, duration_secs, rating, added, direct_source, last_cached_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            )?;
            for r in rows {
                stmt.execute(params![
                    r.episode_id, r.series_id, r.season, r.season_name, r.episode_num, r.title, r.extension,
                    r.stream_url, r.air_date, r.duration, r.duration_secs, r.rating, r.added, r.direct_source, r.cached_at
                ])?;
            }
        }
        tx.commit()?;
        Ok(())
    }
}

pub struct EpisodeRow {
    pub episode_id: String,
    pub series_id: i64,
    pub season: i64,
    pub season_name: String,
    pub episode_num: i64,
    pub title: String,
    pub extension: String,
    pub stream_url: String,
    pub air_date: String,
    pub duration: String,
    pub duration_secs: i64,
    pub rating: Option<f64>,
    pub added: String,
    pub direct_source: String,
    pub cached_at: i64,
}

fn movie_from_row(r: &rusqlite::Row) -> rusqlite::Result<Movie> {
    let url: Option<String> = r.get(5)?;
    let extension = url
        .as_deref()
        .and_then(|u| u.split('?').next())
        .and_then(|u| u.rsplit_once('.'))
        .map(|(_, ext)| ext.to_string())
        .filter(|e| !e.is_empty() && e.len() <= 4 && !e.contains('/'))
        .unwrap_or_else(|| "mp4".into());
    Ok(Movie {
        stream_id: r.get(0)?,
        name: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
        year: r.get::<_, Option<String>>(2)?.filter(|y| !y.is_empty()),
        rating: r.get(3).ok().flatten(),
        genre: r.get::<_, Option<String>>(4)?.filter(|g| !g.is_empty()),
        extension,
    })
}

fn series_from_row(r: &rusqlite::Row) -> rusqlite::Result<Series> {
    let text = |i: usize| -> rusqlite::Result<Option<String>> { Ok(r.get::<_, Option<String>>(i)?.filter(|s| !s.is_empty())) };
    Ok(Series {
        series_id: r.get(0)?,
        name: text(1)?.unwrap_or_default(),
        plot: text(2)?,
        cast: text(3)?,
        genre: text(4)?,
        rating: r.get(5).ok().flatten(),
        category: text(6)?,
    })
}

fn episode_from_row(r: &rusqlite::Row) -> rusqlite::Result<Episode> {
    Ok(Episode {
        episode_id: r.get(0)?,
        series_id: r.get(1)?,
        season: loose_int(r, 2),
        episode_num: loose_int(r, 3),
        title: r.get::<_, Option<String>>(4)?.unwrap_or_default(),
        extension: r.get::<_, Option<String>>(5)?.filter(|e| !e.is_empty()).unwrap_or_else(|| "mp4".into()),
        air_date: r.get::<_, Option<String>>(6)?.filter(|s| !s.is_empty()),
        duration: r.get::<_, Option<String>>(7)?.filter(|s| !s.is_empty()),
    })
}

/// Provider numbers arrive as 3 or "3"; both end up in SQLite.
fn loose_int(r: &rusqlite::Row, i: usize) -> i64 {
    use rusqlite::types::ValueRef;
    match r.get_ref(i) {
        Ok(ValueRef::Integer(n)) => n,
        Ok(ValueRef::Real(f)) => f as i64,
        Ok(ValueRef::Text(t)) => std::str::from_utf8(t).ok().and_then(|t| t.trim().parse().ok()).unwrap_or(0),
        _ => 0,
    }
}

fn channel_from_row(r: &rusqlite::Row) -> rusqlite::Result<Channel> {
    Ok(Channel { stream_id: r.get(0)?, name: r.get::<_, Option<String>>(1)?.unwrap_or_default(), category: r.get(2)? })
}

/// Create the tables if they don't exist yet (a new machine).
pub fn ensure_schema(conn: &Connection) -> Result<()> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS live_streams (stream_id INTEGER PRIMARY KEY, name TEXT, category_id INTEGER,
            stream_url TEXT, category_name TEXT, epg_channel_id TEXT);
         CREATE TABLE IF NOT EXISTS vod_streams (stream_id INTEGER PRIMARY KEY, name TEXT, category_id INTEGER,
            stream_url TEXT, year TEXT, rating REAL, genre TEXT);
         CREATE TABLE IF NOT EXISTS vod_categories (category_id INTEGER PRIMARY KEY, category_name TEXT, parent_id INTEGER);
         CREATE TABLE IF NOT EXISTS account_info (username TEXT, status TEXT, exp_date INTEGER, max_connections TEXT);
         CREATE TABLE IF NOT EXISTS epg (stream_id INTEGER, start_time INTEGER, end_time INTEGER, title TEXT,
            description TEXT, cached_at INTEGER, PRIMARY KEY (stream_id, start_time));
         CREATE TABLE IF NOT EXISTS series_streams (series_id INTEGER PRIMARY KEY, name TEXT, category_id INTEGER,
            cover TEXT, plot TEXT, \"cast\" TEXT, genre TEXT, rating REAL, category_name TEXT);
         CREATE TABLE IF NOT EXISTS series_episodes (episode_id TEXT PRIMARY KEY, series_id INTEGER, season_number INTEGER,
            season_name TEXT, episode_num INTEGER, title TEXT, container_extension TEXT, stream_url TEXT, air_date TEXT,
            duration TEXT, duration_secs INTEGER, rating REAL, added TEXT, direct_source TEXT, last_cached_at INTEGER);
         CREATE TABLE IF NOT EXISTS scheduled_recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, stream_id INTEGER,
            channel_name TEXT, start_time INTEGER, duration INTEGER, output_path TEXT, timer_unit TEXT,
            status TEXT DEFAULT 'pending', created_at INTEGER);
         CREATE INDEX IF NOT EXISTS idx_live_name ON live_streams(name);
         CREATE INDEX IF NOT EXISTS idx_vod_name ON vod_streams(name);
         CREATE INDEX IF NOT EXISTS idx_vod_cat_name ON vod_categories(category_name);
         CREATE INDEX IF NOT EXISTS idx_epg_stream ON epg(stream_id);
         CREATE INDEX IF NOT EXISTS idx_epg_time ON epg(start_time, end_time);
         CREATE INDEX IF NOT EXISTS idx_series_name ON series_streams(name);
         CREATE INDEX IF NOT EXISTS idx_series_episodes_series ON series_episodes(series_id, season_number, episode_num);
         CREATE INDEX IF NOT EXISTS idx_series_episode_lookup ON series_episodes(series_id, season_number, episode_num);",
    )?;
    Ok(())
}

/// A game or event channel, read from its name.
#[derive(Debug, PartialEq)]
pub struct Event {
    /// "FLSP 160", "ESPN+ 266"
    pub channel: String,
    pub sport: Option<String>,
    /// "Chicoutimi Sagueneens vs Rimouski Oceanic"
    pub title: String,
    /// "home" or "away"
    pub feed: Option<String>,
    pub start: Option<chrono::NaiveDateTime>,
}

/// Event channels carry their game in the name, since they have no TV guide:
/// "(FLSP 160) | hockey:  A vs B (Away) (2026-10-18 15:00:05)",
/// "US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET".
pub fn event_info(name: &str) -> Option<Event> {
    let (left, right) = name.split_once(" | ")?;
    let channel = match (left.find('('), left.rfind(')')) {
        (Some(a), Some(b)) if b > a => left[a + 1..b].trim().to_string(),
        _ => left.trim().to_string(),
    };
    let mut rest = right.trim().to_string();
    let mut sport = None;
    if let Some((before, after)) = rest.split_once(':')
        && before.len() <= 20
        && before.chars().all(|c| c.is_alphabetic() || c == ' ')
    {
        sport = Some(before.trim().to_lowercase());
        rest = after.trim().to_string();
    }
    let mut start = None;
    if rest.ends_with(')')
        && let Some(open) = rest.rfind('(')
        && let Ok(t) = chrono::NaiveDateTime::parse_from_str(&rest[open + 1..rest.len() - 1], "%Y-%m-%d %H:%M:%S")
    {
        start = Some(t);
        rest = rest[..open].trim().to_string();
    }
    let mut feed = None;
    for word in ["Home", "Away"] {
        if let Some(stripped) = rest.strip_suffix(&format!("({word})")) {
            feed = Some(word.to_lowercase());
            rest = stripped.trim().to_string();
        }
    }
    let title = rest.split_whitespace().collect::<Vec<_>>().join(" ");
    (!title.is_empty()).then_some(Event { channel, sport, title, feed, start })
}

fn truncate(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let cut: String = text.chars().take(max).collect();
    format!("{}…", cut.trim_end())
}

/// "Sun 18:29", local time, the format JP already knows.
pub fn short_time(ts: i64) -> String {
    Local.timestamp_opt(ts, 0).single().map(|t| t.format("%a %H:%M").to_string()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn db_with(rows: &[(i64, i64, &str)]) -> Db {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE epg (stream_id INTEGER, start_time INTEGER, end_time INTEGER, title TEXT,
             description TEXT, cached_at INTEGER, PRIMARY KEY (stream_id, start_time));",
        )
        .unwrap();
        for (start, end, title) in rows {
            conn.execute("INSERT INTO epg VALUES (1, ?, ?, ?, '', 0)", params![start, end, title]).unwrap();
        }
        Db(conn)
    }

    fn titles(db: &Db, now: i64) -> Vec<(i64, i64, String)> {
        db.guide(1, now, 4).unwrap().into_iter().map(|p| (p.start_ts, p.end_ts, p.title.unwrap())).collect()
    }

    #[test]
    fn game_channels_read_from_their_name() {
        let e = event_info("(FLSP 160) | hockey:  Chicoutimi Sagueneens vs Rimouski Oceanic (Away) (2026-10-18 15:00:05)").unwrap();
        assert_eq!(e.channel, "FLSP 160");
        assert_eq!(e.sport.as_deref(), Some("hockey"));
        assert_eq!(e.title, "Chicoutimi Sagueneens vs Rimouski Oceanic");
        assert_eq!(e.feed.as_deref(), Some("away"));
        assert_eq!(e.start.unwrap().format("%H:%M").to_string(), "15:00");
        let e = event_info("US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET").unwrap();
        assert_eq!(e.channel, "ESPN+ 266");
        assert_eq!(e.title, "Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET");
        assert!(event_info("CA FR: NOOVO V-TELE HD").is_none());
    }

    #[test]
    fn merges_back_to_back_blocks_of_one_show() {
        let db = db_with(&[(0, 100, "News"), (100, 200, "Hockey"), (200, 300, "Hockey"), (300, 400, "Late")]);
        assert_eq!(titles(&db, 50), [(0, 100, "News".into()), (100, 300, "Hockey".into()), (300, 400, "Late".into())]);
    }

    #[test]
    fn overlapping_copies_pick_the_latest_start_like_python() {
        // Seen on TVA Sports 2: "Dans le ring" listed 14:30–18:00 and 15:00–18:00.
        let db = db_with(&[(0, 300, "Ring"), (30, 300, "Ring"), (300, 330, "Pre-game"), (310, 400, "Dup")]);
        assert_eq!(titles(&db, 60), [(30, 300, "Ring".into()), (300, 330, "Pre-game".into())]);
    }
}
