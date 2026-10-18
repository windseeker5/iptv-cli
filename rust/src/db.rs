//! Read-only access to the CLI's SQLite database (`data/iptv.db`).
//!
//! The Python side (timers, refresh jobs) owns this file and writes to it, so kd
//! opens it read-only and never holds a transaction open.

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

fn channel_from_row(r: &rusqlite::Row) -> rusqlite::Result<Channel> {
    Ok(Channel { stream_id: r.get(0)?, name: r.get::<_, Option<String>>(1)?.unwrap_or_default(), category: r.get(2)? })
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
