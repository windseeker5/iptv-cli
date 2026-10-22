//! Download the provider's lists and TV guide into `data/iptv.db`.
//!
//! One lock file (`data/cache/refresh.lock`) and one set of stamps
//! (`data/cache/refresh.json`), so the timers, JP and the screen never refresh at
//! the same time or disagree on how fresh the data is.
//!
//! - channels: live categories + channels (the hourly job)
//! - guide: the full XMLTV guide (twice a day)
//! - everything: also the account, movies and series (a new machine)

use crate::config::Config;
use crate::db::ensure_schema;
use anyhow::{bail, Context, Result};
use quick_xml::events::Event;
use rusqlite::types::Value as Sql;
use rusqlite::{params, Connection};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::BufReader;
use std::path::Path;
use std::process::Command;
use std::time::Instant;

const BROWSER_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36";

#[derive(Clone, Copy, Default)]
pub struct What {
    pub guide: bool,
    pub everything: bool,
}

pub fn run(cfg: &Config, what: What) -> Result<String> {
    let p = &cfg.provider;
    if p.server.is_empty() || p.username.is_empty() {
        bail!("no provider login (IPTV_SERVER_URL, IPTV_USERNAME, IPTV_PASSWORD in {})", cfg.env_file.display());
    }
    let started = Instant::now();
    let cache = cfg.data_dir.join("cache");
    std::fs::create_dir_all(&cache)?;
    // Wait for any other refresh (a timer, JP) to finish first.
    let _lock = Lock::take(&cache.join("refresh.lock"))?;

    let mut conn = Connection::open(cfg.data_dir.join("iptv.db"))?;
    conn.busy_timeout(std::time::Duration::from_secs(30))?;
    conn.pragma_update(None, "journal_mode", "WAL")?;
    ensure_schema(&conn)?;

    let mut done = vec![];
    if what.everything {
        let n = save_catalog(cfg, &mut conn)?;
        done.push(format!("{} movies, {} series", thousands(n.0), thousands(n.1)));
    }
    let channels = save_channels(cfg, &mut conn)?;
    mark(&cache, "channels")?;
    done.insert(0, format!("{} channels", thousands(channels)));
    if what.guide {
        let (programmes, channels) = save_guide(cfg, &mut conn)?;
        mark(&cache, "guide")?;
        done.push(format!("guide: {} shows on {} channels", thousands(programmes), thousands(channels)));
    }
    let seconds = started.elapsed().as_secs_f64();
    crate::log::event(cfg, "refresh", json!({ "guide": what.guide, "everything": what.everything, "seconds": (seconds * 10.0).round() / 10.0, "result": done }));
    Ok(format!("Updated in {seconds:.0} s: {}", done.join(" · ")))
}

/// One player_api call through curl, the login URL-encoded.
pub fn api(cfg: &Config, action: Option<&str>) -> Result<Value> {
    let p = &cfg.provider;
    let mut cmd = Command::new("curl");
    cmd.args(["-sS", "-G", "--fail", "--max-time", "120", "-A", BROWSER_UA])
        .arg(format!("{}/player_api.php", p.server))
        .args(["--data-urlencode", &format!("username={}", p.username)])
        .args(["--data-urlencode", &format!("password={}", p.password)]);
    if let Some(action) = action {
        cmd.args(["--data-urlencode", &format!("action={action}")]);
    }
    let out = cmd.output().context("running curl")?;
    if !out.status.success() {
        bail!("provider: {}", p.redact(String::from_utf8_lossy(&out.stderr).trim()));
    }
    serde_json::from_slice(&out.stdout).with_context(|| format!("the provider sent something that isn't JSON for {}", action.unwrap_or("account")))
}

fn list(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}

/// A JSON value as SQLite stores it: numbers stay numbers, the rest is text.
fn sql(v: &Value) -> Sql {
    match v {
        Value::Null => Sql::Null,
        Value::Bool(b) => Sql::Integer(*b as i64),
        Value::Number(n) => n.as_i64().map(Sql::Integer).unwrap_or_else(|| Sql::Real(n.as_f64().unwrap_or(0.0))),
        Value::String(s) => Sql::Text(s.clone()),
        other => Sql::Text(other.to_string()),
    }
}

fn key(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

fn names(categories: &Value) -> HashMap<String, String> {
    list(categories)
        .iter()
        .map(|c| (key(&c["category_id"]), c["category_name"].as_str().unwrap_or("Unknown").to_string()))
        .collect()
}

fn save_channels(cfg: &Config, conn: &mut Connection) -> Result<usize> {
    let categories = names(&api(cfg, Some("get_live_categories"))?);
    let streams = api(cfg, Some("get_live_streams"))?;
    let p = &cfg.provider;
    let tx = conn.transaction()?;
    tx.execute("DELETE FROM live_streams", [])?;
    {
        let mut insert = tx.prepare(
            "INSERT OR REPLACE INTO live_streams (stream_id, name, category_id, stream_url, category_name, epg_channel_id) VALUES (?, ?, ?, ?, ?, ?)",
        )?;
        for s in list(&streams) {
            let id = key(&s["stream_id"]);
            insert.execute(params![
                sql(&s["stream_id"]),
                sql(&s["name"]),
                sql(&s["category_id"]),
                format!("{}/live/{}/{}/{id}.ts", p.server, p.username, p.password),
                categories.get(&key(&s["category_id"])).cloned().unwrap_or_else(|| "Unknown".into()),
                // Like the Python job: a missing id is "", a null one stays NULL.
                s.get("epg_channel_id").map(sql).unwrap_or_else(|| Sql::Text(String::new())),
            ])?;
        }
    }
    tx.commit()?;
    Ok(list(&streams).len())
}

/// Account, movies and series (the full "Update Database" of the old TUI).
fn save_catalog(cfg: &Config, conn: &mut Connection) -> Result<(usize, usize)> {
    let account = api(cfg, None)?;
    let vod_categories = api(cfg, Some("get_vod_categories"))?;
    let vod = api(cfg, Some("get_vod_streams"))?;
    let series_categories = names(&api(cfg, Some("get_series_categories"))?);
    let series = api(cfg, Some("get_series"))?;
    let p = &cfg.provider;

    let tx = conn.transaction()?;
    for table in ["account_info", "vod_categories", "vod_streams", "series_streams", "series_episodes"] {
        tx.execute(&format!("DELETE FROM {table}"), [])?;
    }
    let user = &account["user_info"];
    tx.execute(
        "INSERT INTO account_info VALUES (?, ?, ?, ?)",
        params![sql(&user["username"]), sql(&user["status"]), sql(&user["exp_date"]), sql(&user["max_connections"])],
    )?;
    {
        let mut insert = tx.prepare("INSERT OR REPLACE INTO vod_categories VALUES (?, ?, ?)")?;
        for c in list(&vod_categories) {
            insert.execute(params![sql(&c["category_id"]), sql(&c["category_name"]), sql(&c["parent_id"])])?;
        }
        let mut insert = tx.prepare(
            "INSERT OR REPLACE INTO vod_streams (stream_id, name, category_id, stream_url, year, rating, genre) VALUES (?, ?, ?, ?, ?, ?, ?)",
        )?;
        for s in list(&vod) {
            let ext = s["container_extension"].as_str().filter(|e| !e.is_empty()).unwrap_or("mp4");
            insert.execute(params![
                sql(&s["stream_id"]),
                sql(&s["name"]),
                sql(&s["category_id"]),
                format!("{}/movie/{}/{}/{}.{ext}", p.server, p.username, p.password, key(&s["stream_id"])),
                sql(&s["year"]),
                sql(&s["rating"]),
                sql(&s["genre"]),
            ])?;
        }
        let mut insert = tx.prepare(
            "INSERT OR REPLACE INTO series_streams (series_id, name, category_id, cover, plot, \"cast\", genre, rating, category_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )?;
        for s in list(&series) {
            insert.execute(params![
                sql(&s["series_id"]),
                sql(&s["name"]),
                sql(&s["category_id"]),
                sql(&s["cover"]),
                sql(&s["plot"]),
                sql(&s["cast"]),
                sql(&s["genre"]),
                sql(&s["rating"]),
                series_categories.get(&key(&s["category_id"])).cloned().unwrap_or_else(|| "Unknown".into()),
            ])?;
        }
    }
    tx.commit()?;
    Ok((list(&vod).len(), list(&series).len()))
}

/// The full XMLTV guide: download to a file, read it as a stream (it is large),
/// keep the shows of channels we know. Returns (shows, channels).
fn save_guide(cfg: &Config, conn: &mut Connection) -> Result<(usize, usize)> {
    let p = &cfg.provider;
    let file = cfg.data_dir.join("cache/xmltv.xml.part");
    let out = Command::new("curl")
        .args(["-sS", "-G", "--fail", "--compressed", "--max-time", "300", "-A", BROWSER_UA, "-o"])
        .arg(&file)
        .arg(format!("{}/xmltv.php", p.server))
        .args(["--data-urlencode", &format!("username={}", p.username)])
        .args(["--data-urlencode", &format!("password={}", p.password)])
        .output()
        .context("running curl")?;
    if !out.status.success() {
        let _ = std::fs::remove_file(&file);
        bail!("guide download: {}", p.redact(String::from_utf8_lossy(&out.stderr).trim()));
    }

    let mut channel_streams: HashMap<String, Vec<i64>> = HashMap::new();
    {
        let mut stmt = conn.prepare("SELECT stream_id, epg_channel_id FROM live_streams WHERE epg_channel_id IS NOT NULL AND epg_channel_id != ''")?;
        let rows = stmt.query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))?;
        for row in rows {
            let (id, channel) = row?;
            channel_streams.entry(channel).or_default().push(id);
        }
    }
    if channel_streams.is_empty() {
        bail!("no channel in the list has a guide id; update the channel list first");
    }

    let shows = read_xmltv(&file, &channel_streams);
    let _ = std::fs::remove_file(&file);
    let shows = shows?;
    if shows.is_empty() {
        bail!("the guide had no shows for your channels");
    }

    let now = chrono::Utc::now().timestamp();
    let mut channels: Vec<i64> = vec![];
    let tx = conn.transaction()?;
    {
        let mut insert = tx.prepare("INSERT OR REPLACE INTO epg (stream_id, start_time, end_time, title, description, cached_at) VALUES (?, ?, ?, ?, ?, ?)")?;
        for s in &shows {
            for id in &channel_streams[&s.channel] {
                insert.execute(params![id, s.start, s.stop, s.title, s.desc, now])?;
                channels.push(*id);
            }
        }
    }
    // Keep the cache tidy: shows that ended more than an hour ago go.
    tx.execute("DELETE FROM epg WHERE end_time < ?", params![now - 3600])?;
    tx.commit()?;
    let rows = channels.len();
    channels.sort_unstable();
    channels.dedup();
    Ok((rows, channels.len()))
}

struct Show {
    channel: String,
    start: i64,
    stop: i64,
    title: String,
    desc: String,
}

fn read_xmltv(path: &Path, wanted: &HashMap<String, Vec<i64>>) -> Result<Vec<Show>> {
    let mut reader = quick_xml::Reader::from_reader(BufReader::new(std::fs::File::open(path)?));
    reader.config_mut().trim_text(false);
    let mut buf = Vec::new();
    let mut shows = vec![];
    let mut current: Option<Show> = None;
    let mut field: Option<&'static str> = None;
    loop {
        match reader.read_event_into(&mut buf).context("reading the guide")? {
            Event::Start(e) if e.name().as_ref() == b"programme" => {
                let mut show = Show { channel: String::new(), start: 0, stop: 0, title: String::new(), desc: String::new() };
                for a in e.attributes().flatten() {
                    let value = a.unescape_value().unwrap_or_default().to_string();
                    match a.key.as_ref() {
                        b"channel" => show.channel = value,
                        b"start" => show.start = xmltv_time(&value).unwrap_or(0),
                        b"stop" => show.stop = xmltv_time(&value).unwrap_or(0),
                        _ => {}
                    }
                }
                current = (wanted.contains_key(&show.channel) && show.start > 0 && show.stop > 0).then_some(show);
            }
            Event::Start(e) if current.is_some() => {
                field = match e.name().as_ref() {
                    // Several titles (languages): the last one wins, like the Python job.
                    b"title" => {
                        if let Some(s) = current.as_mut() {
                            s.title.clear();
                        }
                        Some("title")
                    }
                    b"desc" => {
                        if let Some(s) = current.as_mut() {
                            s.desc.clear();
                        }
                        Some("desc")
                    }
                    _ => None,
                };
            }
            Event::Text(t) => {
                if let (Some(show), Some(f)) = (current.as_mut(), field) {
                    let text = t.unescape().map(|c| c.into_owned()).unwrap_or_default();
                    if f == "title" { show.title.push_str(&text) } else { show.desc.push_str(&text) }
                }
            }
            Event::CData(t) => {
                if let (Some(show), Some(f)) = (current.as_mut(), field) {
                    let text = String::from_utf8_lossy(&t).into_owned();
                    if f == "title" { show.title.push_str(&text) } else { show.desc.push_str(&text) }
                }
            }
            Event::End(e) => match e.name().as_ref() {
                b"programme" => {
                    if let Some(show) = current.take() {
                        shows.push(show);
                    }
                    field = None;
                }
                b"title" | b"desc" => field = None,
                _ => {}
            },
            Event::Eof => break,
            _ => {}
        }
        buf.clear();
    }
    Ok(shows)
}

/// "20251018180000 +0000" (or without an offset, meaning UTC) → seconds since 1970.
fn xmltv_time(text: &str) -> Option<i64> {
    let text = text.trim();
    if text.len() >= 19 && matches!(text.as_bytes().get(15), Some(b'+' | b'-')) {
        return chrono::DateTime::parse_from_str(&text[..20.min(text.len())], "%Y%m%d%H%M%S %z").ok().map(|t| t.timestamp());
    }
    chrono::NaiveDateTime::parse_from_str(text.get(..14)?, "%Y%m%d%H%M%S").ok().map(|t| t.and_utc().timestamp())
}

/// Write when this part was last downloaded (`refresh.json`, read by everyone).
fn mark(cache: &Path, kind: &str) -> Result<()> {
    let path = cache.join("refresh.json");
    let mut stamps: serde_json::Map<String, Value> =
        std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default();
    stamps.insert(kind.into(), json!(chrono::Utc::now().timestamp()));
    let tmp = cache.join("refresh.json.tmp");
    std::fs::write(&tmp, Value::Object(stamps).to_string())?;
    std::fs::rename(tmp, path)?;
    Ok(())
}

fn thousands(n: usize) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// An exclusive lock file (`flock`), held for as long as the file stays open.
struct Lock(#[allow(dead_code)] std::fs::File);

impl Lock {
    fn take(path: &Path) -> Result<Lock> {
        use std::os::fd::AsRawFd;
        let file = std::fs::OpenOptions::new().create(true).truncate(false).write(true).open(path)?;
        // SAFETY: flock on a file descriptor we own; it is released when the file closes.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } != 0 {
            bail!("could not lock {}", path.display());
        }
        Ok(Lock(file))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xmltv_times_like_python() {
        assert_eq!(xmltv_time("20250811180000 +0000"), Some(1754935200));
        assert_eq!(xmltv_time("20250811140000 -0400"), Some(1754935200));
        assert_eq!(xmltv_time("20250811180000"), Some(1754935200));
        assert_eq!(xmltv_time(""), None);
    }

    #[test]
    fn reads_a_guide() {
        let dir = std::env::temp_dir().join(format!("kdtv-xmltv-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("g.xml");
        std::fs::write(
            &path,
            r#"<?xml version="1.0"?><tv><channel id="noovo.ca"><display-name>NOOVO</display-name></channel>
<programme start="20251018222900 +0000" stop="20251019010200 +0000" channel="noovo.ca"><title lang="fr">Occupation Double &amp; co</title><desc>Télé-réalité</desc></programme>
<programme start="20251018222900 +0000" stop="20251019010200 +0000" channel="other"><title>Skip</title></programme></tv>"#,
        )
        .unwrap();
        let wanted = HashMap::from([("noovo.ca".to_string(), vec![412858])]);
        let shows = read_xmltv(&path, &wanted).unwrap();
        assert_eq!(shows.len(), 1);
        assert_eq!(shows[0].title, "Occupation Double & co");
        assert_eq!(shows[0].desc, "Télé-réalité");
        assert_eq!(shows[0].stop - shows[0].start, 153 * 60);
        std::fs::remove_dir_all(dir).unwrap();
    }
}
