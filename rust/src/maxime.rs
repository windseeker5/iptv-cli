//! Maxime's hockey games: the UMass Minutemen schedule, and where we can watch each game.
//!
//! Maxim Massé (#7, Rimouski) plays for the University of Massachusetts Amherst
//! (NCAA Division I, Hockey East). His father Luc sent the team facts and the
//! 2026-27 schedule (PDF); the schedule is refreshed twice a day from Hockey East's
//! site (`kdtv maxime --refresh`) so times, TV networks and results stay current.
//! JP's maxime_games tool reads that file plus our own channel list and guide, so
//! "when is Maxime's next game, and do we have it?" is one fast look-up.

use crate::config::Config;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Datelike, Duration, Local, NaiveDate, NaiveDateTime, TimeZone};
use regex::Regex;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::LazyLock;

const SCHEDULE_URL: &str = "https://hockeyeastonline.com/men/massachusetts/2026-27-schedule.php";
const WATCH_URL: &str = "https://hockeyeastonline.com/watch/index.php";

/// The PDF Luc sent (Hockey East, printed 2026-09-22): used until the first refresh,
/// or if the site moves. (date, "at" / "vs." / "", opponent as printed, time as printed, footnote)
const SEED: &[(&str, &str, &str, &str, &str)] = &[
    ("2026-10-02", "at", "Colorado College (nc)", "7:00 MT", ""), ("2026-10-03", "at", "Colorado College (nc)", "6:00 MT", ""),
    ("2026-10-09", "", "RIT (nc)", "7:00 ET", ""), ("2026-10-10", "", "RIT (nc)", "7:00 ET", ""),
    ("2026-10-16", "", "Northeastern", "", ""), ("2026-10-17", "", "Northeastern (nc)", "", ""),
    ("2026-10-24", "", "Stonehill (nc)", "7:00 ET", ""), ("2026-10-29", "at", "Harvard (nc)", "7:00 ET", ""),
    ("2026-10-30", "", "Harvard (nc)", "7:00 ET", ""), ("2026-11-06", "at", "Maine", "7:00 ET", ""),
    ("2026-11-07", "at", "Maine", "7:00 ET", ""), ("2026-11-13", "", "Boston College", "", ""),
    ("2026-11-14", "at", "Boston College", "7:00 ET", ""), ("2026-11-19", "", "Providence", "7:00 ET", ""),
    ("2026-11-21", "at", "Providence", "", ""), ("2026-12-04", "", "Northeastern", "", ""),
    ("2026-12-05", "", "Northeastern", "", ""), ("2026-12-11", "at", "Merrimack", "7:00 ET", ""),
    ("2026-12-12", "", "Merrimack", "", ""),
    ("2026-12-29", "vs.", "Arizona State (nc)", "4:00 CT", "Holiday Face-Off – Fiserv Forum, Milwaukee, Wis."),
    ("2026-12-30", "vs.", "TBD (nc)", "", "Holiday Face-Off – Fiserv Forum, Milwaukee, Wis."),
    ("2027-01-08", "at", "Boston University", "7:00 ET", ""), ("2027-01-09", "", "Boston University", "", ""),
    ("2027-01-22", "", "Vermont", "", ""), ("2027-01-23", "", "Vermont", "", ""),
    ("2027-02-02", "", "UMass Lowell", "", ""), ("2027-02-06", "at", "New Hampshire", "7:00 ET", ""),
    ("2027-02-12", "", "New Hampshire", "", ""), ("2027-02-13", "", "New Hampshire", "", ""),
    ("2027-02-19", "", "UMass Lowell", "", ""), ("2027-02-20", "at", "UMass Lowell", "6:05 ET", ""),
    ("2027-02-25", "", "UConn", "", ""), ("2027-02-27", "at", "UConn", "2:00 ET", ""),
    ("2027-03-05", "at", "Boston College", "7:00 ET", ""),
];

/// Other ways teams are written in channel names and guide titles.
const ALIASES: &[(&str, &[&str])] = &[
    ("Boston University", &["Boston University", "Boston U", "BU"]),
    ("Boston College", &["Boston College", "BC"]),
    ("Arizona State", &["Arizona State", "Arizona St"]),
    ("UConn", &["UConn", "Connecticut"]),
    ("New Hampshire", &["New Hampshire", "UNH"]),
    ("RIT", &["RIT", "Rochester"]),
    ("Colorado College", &["Colorado College", "Colorado Coll"]),
];

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

static OTHER_SPORTS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)football|field hockey|women|volleyball|soccer|basketball|lacrosse|\bfc:"));
static MASSACHUSETTS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bmassachusetts\b"));
static UMASS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\bumass\b"));
/// "UMass" also means these other teams; checked right after each "UMass".
static OTHER_UMASS: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^\s*[-_ ]?\s*(lowell|boston|dartmouth)"));

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug)]
struct Game {
    date: String,
    #[serde(rename = "where")]
    place: String,
    opponent: String,
    #[serde(default)]
    non_conference: bool,
    #[serde(default)]
    time: String,
    #[serde(default)]
    tv: String,
    #[serde(default)]
    result: String,
    #[serde(default)]
    note: String,
}

#[derive(Serialize, Deserialize)]
struct ScheduleFile {
    fetched_at: Option<i64>,
    source: String,
    #[serde(default)]
    record: String,
    games: Vec<Game>,
}

fn game(date: &str, place: &str, opponent: &str, time: &str, note: &str, tv: &str, result: &str) -> Game {
    let place = match place {
        "at" => "away",
        "vs." => "neutral",
        _ => "home",
    };
    Game {
        date: date.into(),
        place: place.into(),
        opponent: opponent.replace("(nc)", "").trim().into(),
        non_conference: opponent.contains("(nc)"),
        time: time.into(),
        tv: tv.into(),
        result: result.into(),
        note: note.into(),
    }
}

fn schedule_file(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("maxime").join("schedule.json")
}

// ── the refresh (`kdtv maxime --refresh`, twice a day) ────────────────────

/// Text of an HTML cell: tags dropped, entities decoded, spaces collapsed.
fn cell(text: &str) -> String {
    static TAG: LazyLock<Regex> = LazyLock::new(|| re(r"<[^>]+>"));
    static SPACE: LazyLock<Regex> = LazyLock::new(|| re(r"\s+"));
    SPACE.replace_all(&unescape(&TAG.replace_all(text, " ")), " ").trim().to_string()
}

/// The entities Hockey East's pages use; numeric ones too.
fn unescape(text: &str) -> String {
    static ENTITY: LazyLock<Regex> = LazyLock::new(|| re(r"&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z]+);"));
    ENTITY
        .replace_all(text, |c: &regex::Captures| {
            let name = &c[1];
            let ch = if let Some(hex) = name.strip_prefix("#x").or_else(|| name.strip_prefix("#X")) {
                u32::from_str_radix(hex, 16).ok().and_then(char::from_u32)
            } else if let Some(dec) = name.strip_prefix('#') {
                dec.parse().ok().and_then(char::from_u32)
            } else {
                match name {
                    "amp" => Some('&'),
                    "lt" => Some('<'),
                    "gt" => Some('>'),
                    "quot" => Some('"'),
                    "apos" => Some('\''),
                    "nbsp" => Some('\u{a0}'),
                    "ndash" => Some('–'),
                    "mdash" => Some('—'),
                    "rsquo" => Some('’'),
                    "lsquo" => Some('‘'),
                    "rdquo" => Some('”'),
                    "ldquo" => Some('“'),
                    "eacute" => Some('é'),
                    _ => None,
                }
            };
            ch.map(String::from).unwrap_or_else(|| c[0].to_string())
        })
        .into_owned()
}

/// Rows of Hockey East's team schedule table: month headers, then one row per game.
fn parse_schedule(page: &str) -> Vec<Game> {
    static NOTE: LazyLock<Regex> = LazyLock::new(|| re(r"<b>(\d+)</b>(.*?)<br"));
    static ROW: LazyLock<Regex> = LazyLock::new(|| re(r"(?s)<tr\b.*?</tr>"));
    static HEAD: LazyLock<Regex> = LazyLock::new(|| re(r#"stats-section"><td[^>]*>\s*(\w+) (\d{4})"#));
    static TD: LazyLock<Regex> = LazyLock::new(|| re(r"<td\b[^>]*>"));
    static DAY: LazyLock<Regex> = LazyLock::new(|| re(r"^\d\d? \w{3}$"));
    let notes: HashMap<String, String> = NOTE.captures_iter(page).map(|c| (c[1].to_string(), cell(&c[2]))).collect();
    let mut games = vec![];
    let (mut year, mut month) = (None::<i32>, 0u32);
    for row in ROW.find_iter(page).map(|m| m.as_str()) {
        if let Some(h) = HEAD.captures(row) {
            if let Some(m) = month_number(&h[1]) {
                month = m;
                year = h[2].parse().ok();
            }
            continue;
        }
        let cells: Vec<String> = TD.split(row).skip(1).map(cell).collect();
        let Some(y) = year else { continue };
        if cells.len() < 10 || !DAY.is_match(&cells[0]) {
            continue;
        }
        let Some(date) = cells[0].split_whitespace().next().and_then(|d| d.parse().ok()).and_then(|d| NaiveDate::from_ymd_opt(y, month, d))
        else {
            continue;
        };
        // cells: date, footnote, W/L/T, our goals, "- their goals", ot, at/vs., opponent, TV, time or box score
        let result = if cells[2].is_empty() {
            String::new()
        } else {
            let ot = if cells[5].is_empty() { String::new() } else { format!(" {}", cells[5]) };
            format!("{} {}{}{ot}", cells[2], cells[3], cells[4].replace(' ', ""))
        };
        let note = notes.get(&cells[1]).cloned().unwrap_or_default();
        games.push(game(&date.format("%Y-%m-%d").to_string(), &cells[6], &cells[7], &time_in(row), &note, &tv_in(row), &result));
    }
    games
}

fn month_number(name: &str) -> Option<u32> {
    const MONTHS: [&str; 12] =
        ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];
    MONTHS.iter().position(|m| m.eq_ignore_ascii_case(name)).map(|i| i as u32 + 1)
}

fn tv_in(row: &str) -> String {
    static TV: LazyLock<Regex> = LazyLock::new(|| re(r#"(?s)class="left tvlist">(.*?)</td>"#));
    TV.captures(row).map(|c| cell(&c[1])).unwrap_or_default()
}

fn time_in(row: &str) -> String {
    static TIME: LazyLock<Regex> = LazyLock::new(|| re(r"(\d{1,2}:\d\d)\s*(ET|CT|MT|PT)"));
    let text = unescape(row).replace('\u{a0}', " ");
    TIME.captures(&text).map(|c| format!("{} {}", &c[1], &c[2])).unwrap_or_default()
}

/// Download the official schedule and keep it if it looks sane (else keep the last good copy).
pub fn refresh(cfg: &Config) -> Result<Value> {
    let out = Command::new("curl")
        .args(["-sS", "--fail", "-L", "--max-time", "30", "-A", "Mozilla/5.0 (KD CLI; JP)", SCHEDULE_URL])
        .output()
        .context("running curl")?;
    if !out.status.success() {
        bail!("Hockey East: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    let page = String::from_utf8_lossy(&out.stdout);
    let games = parse_schedule(&page);
    if games.len() < 20 {
        return Ok(json!({ "ok": false, "error": format!("only {} games parsed; kept the previous schedule", games.len()) }));
    }
    static RECORD: LazyLock<Regex> = LazyLock::new(|| re(r"([\d-]+ Overall \([^)]*\))"));
    let record = RECORD.captures(&page).map(|c| c[1].to_string()).unwrap_or_default();
    let count = games.len();
    let file = ScheduleFile { fetched_at: Some(chrono::Utc::now().timestamp()), source: SCHEDULE_URL.into(), record: record.clone(), games };
    write_schedule(&schedule_file(cfg), &file)?;
    Ok(json!({ "ok": true, "games": count, "record": record }))
}

fn write_schedule(path: &Path, file: &ScheduleFile) -> Result<()> {
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")))?;
    let mut text = vec![];
    let mut ser = serde_json::Serializer::with_formatter(&mut text, serde_json::ser::PrettyFormatter::with_indent(b" "));
    file.serialize(&mut ser)?;
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, text)?;
    std::fs::rename(&tmp, path).with_context(|| format!("writing {}", path.display()))
}

// ── JP's look-up ──────────────────────────────────────────────────────────

fn load(cfg: &Config) -> ScheduleFile {
    if let Some(data) = std::fs::read_to_string(schedule_file(cfg)).ok().and_then(|t| serde_json::from_str::<ScheduleFile>(&t).ok())
        && data.games.len() >= 20
    {
        return data;
    }
    ScheduleFile {
        fetched_at: None,
        source: "the PDF Luc sent (2026-09-22)".into(),
        record: String::new(),
        games: SEED.iter().map(|(d, w, o, t, n)| game(d, w, o, t, n, "", "")).collect(),
    }
}

/// Puck drop in local time, or None when no time is published yet.
fn start(g: &Game) -> Option<NaiveDateTime> {
    static TIME: LazyLock<Regex> = LazyLock::new(|| re(r"^(\d{1,2}):(\d\d) (\w\w)"));
    let c = TIME.captures(&g.time)?;
    let (h, mi): (u32, u32) = (c[1].parse().ok()?, c[2].parse().ok()?);
    let h = if h == 12 { h } else { h + 12 }; // printed without AM/PM; college puck drops are afternoon/evening
    let zone: chrono_tz::Tz = match &c[3] {
        "CT" => chrono_tz::America::Chicago,
        "MT" => chrono_tz::America::Denver,
        "PT" => chrono_tz::America::Los_Angeles,
        _ => chrono_tz::America::New_York,
    };
    let day = NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").ok()?;
    let when = zone.from_local_datetime(&day.and_hms_opt(h, mi, 0)?).earliest()?;
    Some(when.with_timezone(&Local).naive_local())
}

fn opponent_pattern(opponent: &str) -> Option<Regex> {
    if opponent.to_uppercase().starts_with("TBD") {
        return None;
    }
    let names: Vec<&str> = ALIASES.iter().find(|(k, _)| *k == opponent).map(|(_, v)| v.to_vec()).unwrap_or_else(|| vec![opponent]);
    let alternation = names.iter().map(|n| regex::escape(n)).collect::<Vec<_>>().join("|");
    Regex::new(&format!(r"(?i)\b({alternation})\b")).ok()
}

fn is_our_game(text: &str, opponent: &str) -> bool {
    if OTHER_SPORTS.is_match(text) {
        return false;
    }
    let mut text = text.to_string();
    if let Some(opp) = opponent_pattern(opponent) {
        if !opp.is_match(&text) {
            return false;
        }
        // "UMass Lowell vs. UMass": drop the opponent, our team must still be there.
        text = opp.replace_all(&text, " ").into_owned();
    }
    MASSACHUSETTS.is_match(&text) || UMASS.find_iter(&text).any(|m| !OTHER_UMASS.is_match(&text[m.end()..]))
}

/// A date written in a channel name: 2026-10-17, "Oct 17" or "17 Oct".
fn date_in(text: &str, year: i32) -> Option<NaiveDate> {
    static ISO: LazyLock<Regex> = LazyLock::new(|| re(r"\b(20\d\d)-(\d\d)-(\d\d)\b"));
    static MONTH_DAY: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* (\d{1,2})\b"));
    static DAY_MONTH: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b"));
    let month = |m: &str| month_number_short(m);
    if let Some(c) = ISO.captures(text)
        && let Some(d) = NaiveDate::from_ymd_opt(c[1].parse().ok()?, c[2].parse().ok()?, c[3].parse().ok()?)
    {
        return Some(d);
    }
    if let Some(c) = MONTH_DAY.captures(text)
        && let Some(d) = NaiveDate::from_ymd_opt(year, month(&c[1])?, c[2].parse().ok()?)
    {
        return Some(d);
    }
    if let Some(c) = DAY_MONTH.captures(text)
        && let Some(d) = NaiveDate::from_ymd_opt(year, month(&c[2])?, c[1].parse().ok()?)
    {
        return Some(d);
    }
    None
}

fn month_number_short(m: &str) -> Option<u32> {
    const MONTHS: [&str; 12] = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
    MONTHS.iter().position(|x| x.eq_ignore_ascii_case(m)).map(|i| i as u32 + 1)
}

/// Our channels (event channel names and guide titles) carrying each of these games, keyed by date.
fn channels_for(cfg: &Config, games: &[&Game]) -> HashMap<String, Vec<Value>> {
    let mut found: HashMap<String, Vec<Value>> = games.iter().map(|g| (g.date.clone(), vec![])).collect();
    let Some(first) = games.iter().filter_map(|g| NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").ok()).min() else {
        return found;
    };
    let since = local_ts(first.and_hms_opt(0, 0, 0).unwrap_or_default()) - 6 * 3600;
    let Ok(conn) = Connection::open_with_flags(cfg.data_dir.join("iptv.db"), OpenFlags::SQLITE_OPEN_READ_ONLY) else {
        return found;
    };
    let names: Vec<(i64, String, Option<String>)> = conn
        .prepare("SELECT stream_id, name, category_name FROM live_streams WHERE name LIKE '%massachusetts%' OR name LIKE '%umass%'")
        .and_then(|mut s| s.query_map([], |r| Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default(), r.get(2)?)))?.collect())
        .unwrap_or_default();
    let guide: Vec<(i64, String, String, i64)> = conn
        .prepare(
            "SELECT e.stream_id, l.name, e.title, e.start_time FROM epg e JOIN live_streams l USING(stream_id) \
             WHERE e.end_time > ? AND (e.title LIKE '%massachusetts%' OR e.title LIKE '%umass%')",
        )
        .and_then(|mut s| {
            s.query_map([since], |r| {
                Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default(), r.get::<_, Option<String>>(2)?.unwrap_or_default(), r.get(3)?))
            })?
            .collect()
        })
        .unwrap_or_default();
    for g in games {
        let Ok(gday) = NaiveDate::parse_from_str(&g.date, "%Y-%m-%d") else { continue };
        let first_against = games.iter().find(|x| x.opponent == g.opponent).is_some_and(|x| std::ptr::eq(*x, *g));
        let list = found.entry(g.date.clone()).or_default();
        for (stream_id, name, category) in &names {
            if !is_our_game(name, &g.opponent) {
                continue;
            }
            let d = date_in(name, gday.year());
            if d == Some(gday) || (d.is_none() && first_against) {
                list.push(json!({ "stream_id": stream_id, "channel": name, "category": category, "how": "event channel" }));
            }
        }
        for (stream_id, name, title, start_time) in &guide {
            let Some(starts) = Local.timestamp_opt(*start_time, 0).single() else { continue };
            if starts.date_naive() == gday && is_our_game(title, &g.opponent) {
                list.push(json!({ "stream_id": stream_id, "channel": name, "how": format!("guide: {title} at {}", starts.format("%H:%M")) }));
            }
        }
        list.truncate(6);
    }
    found
}

fn local_ts(t: NaiveDateTime) -> i64 {
    Local.from_local_datetime(&t).earliest().map(|d: DateTime<Local>| d.timestamp()).unwrap_or_else(|| t.and_utc().timestamp())
}

/// Next games (with the channels we have for any in the next 3 days) and the latest results.
pub fn games(cfg: &Config, upcoming: usize, past: usize) -> Value {
    games_at(cfg, Local::now().naive_local(), upcoming, past)
}

fn games_at(cfg: &Config, now: NaiveDateTime, upcoming: usize, past: usize) -> Value {
    let data = load(cfg);
    let rows: Vec<(&Game, Option<NaiveDateTime>, NaiveDateTime)> = data
        .games
        .iter()
        .map(|g| {
            let s = start(g);
            let end = match s {
                Some(s) => s + Duration::hours(3),
                None => NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").map(|d| d.and_hms_opt(0, 0, 0).unwrap_or_default() + Duration::days(1)).unwrap_or(now),
            };
            (g, s, end)
        })
        .collect();
    let done: Vec<&Game> = rows.iter().filter(|(g, _, end)| !g.result.is_empty() || *end < now).map(|(g, _, _)| *g).collect();
    let next: Vec<&(&Game, Option<NaiveDateTime>, NaiveDateTime)> =
        rows.iter().filter(|(g, _, end)| g.result.is_empty() && *end >= now).take(upcoming.clamp(1, 10)).collect();
    let horizon = now.date() + Duration::days(3);
    let soon: Vec<&Game> =
        next.iter().map(|(g, _, _)| *g).filter(|g| NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").is_ok_and(|d| d <= horizon)).collect();
    let channels = channels_for(cfg, &soon);

    let next_games: Vec<Value> = next
        .iter()
        .map(|(g, s, _)| {
            let mut item = without_empty(g);
            match s {
                Some(s) => item.insert("local_start".into(), s.format("%a %Y-%m-%d %H:%M").to_string().into()),
                None => {
                    let day = NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").map(|d| d.format("%a %Y-%m-%d").to_string()).unwrap_or_default();
                    item.insert("local_start".into(), format!("{day}, time not published yet").into())
                }
            };
            let ours = if soon.iter().any(|x| std::ptr::eq(*x, *g)) {
                match channels.get(&g.date) {
                    Some(list) if !list.is_empty() => Value::Array(list.clone()),
                    _ => "none found yet".into(),
                }
            } else {
                "not checked (event channels only appear a day or so before the game)".into()
            };
            item.insert("our_channels".into(), ours);
            Value::Object(item)
        })
        .collect();

    let past = past.min(10);
    let latest: Vec<Value> = if past == 0 {
        vec![]
    } else {
        done[done.len().saturating_sub(past)..]
            .iter()
            .filter(|g| !g.result.is_empty())
            .map(|g| json!({ "date": g.date, "where": g.place, "opponent": g.opponent, "result": g.result, "non_conference": g.non_conference }))
            .collect()
    };
    let age_hours = data.fetched_at.map(|f| ((chrono::Utc::now().timestamp() - f) as f64 / 3600.0 * 10.0).round() / 10.0);
    json!({
        "team": "UMass Minutemen (Massachusetts / UMass Amherst), NCAA D1, Hockey East",
        "record": if data.record.is_empty() { Value::Null } else { data.record.clone().into() },
        "next_games": next_games,
        "latest_results": latest,
        "schedule_source": data.source,
        "schedule_age_hours": age_hours,
        "channel_list_age_minutes": crate::status::list_age_minutes(&cfg.data_dir).0,
        "official_streams": format!("Hockey East games: {WATCH_URL} (ESPN+ in the US). The `tv` field is the TV network, when the schedule lists one."),
        "times": "local_start is already in local time (Eastern); the `time` field is as printed by Hockey East.",
    })
}

/// A game's fields, minus the empty ones (like the Python `v not in ("", None)`).
fn without_empty(g: &Game) -> Map<String, Value> {
    match serde_json::to_value(g) {
        Ok(Value::Object(m)) => m.into_iter().filter(|(_, v)| v.as_str() != Some("")).collect(),
        _ => Map::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PAGE: &str = r#"<p class="storysub18">2-3-1 Overall (1-0-0/0-0-0 Hockey East)</p>
<table border="0" width="100%" class="data schedule">
<tbody>
<tr class="stats-section"><td colspan="99">October 2026</td></tr>
<tr valign="top">
	<td>09&nbsp;Fri	<td align="center"><sup></sup></td>
	<td class="center result T">T	<td>2	<td class="left" nowrap="nowrap">- 2</td>
	<td class="left" width="25">ot</td>
	<td nowrap="nowrap" class="center" width="20"></td>
	<td nowrap="nowrap" class="left">RIT (nc)</td>
	<td nowrap="nowrap" class="left tvlist"></td>
		<td><a href="/box/final/20261009/rit/uma/m">Box</a></td>
	<td class="nochi"><a href="/men/gameday/20261009.php">Gameday</a></td>
</tr>
<tr class="stats-section"><td colspan="99">November 2026</td></tr>
<tr valign="top">
	<td>07&nbsp;Sat	<td align="center"><sup></sup></td>
	<td class="center result ">	<td>	<td class="left" nowrap="nowrap"></td>
	<td class="left" width="25"></td>
	<td nowrap="nowrap" class="center" width="20">at</td>
	<td nowrap="nowrap" class="left">Maine</td>
	<td nowrap="nowrap" class="left tvlist">NESN</td>
		<td nowrap="nowrap">&nbsp;7:00 ET&nbsp;</td>
	<td class="nochi"><a href="/watch/index.php">Watch</a></td>
</tr>
<tr class="stats-section"><td colspan="99">December 2026</td></tr>
<tr valign="top">
	<td>29&nbsp;Tue	<td align="center"><sup>1</sup></td>
	<td class="center result ">	<td>	<td class="left" nowrap="nowrap"></td>
	<td class="left" width="25"></td>
	<td nowrap="nowrap" class="center" width="20">vs.</td>
	<td nowrap="nowrap" class="left">Arizona State (nc)</td>
	<td nowrap="nowrap" class="left tvlist"></td>
		<td nowrap="nowrap">&nbsp;4:00 CT&nbsp;</td>
	<td class="nochi"><a href="/watch/index.php">Watch</a></td>
</tr>
</tbody></table>
		<b>1</b> Holiday Face-Off  &ndash; Fiserv Forum, Milwaukee, Wis.<br />
"#;

    #[test]
    fn parses_hockey_east_rows() {
        let games = parse_schedule(PAGE);
        assert_eq!(games.len(), 3);
        assert_eq!(games[0], game("2026-10-09", "", "RIT (nc)", "", "", "", "T 2-2 ot"));
        assert_eq!(games[1], game("2026-11-07", "at", "Maine", "7:00 ET", "", "NESN", ""));
        assert_eq!(
            games[2],
            game("2026-12-29", "vs.", "Arizona State (nc)", "4:00 CT", "Holiday Face-Off – Fiserv Forum, Milwaukee, Wis.", "", "")
        );
        assert!(games[2].non_conference && games[2].place == "neutral");
    }

    #[test]
    fn spots_our_umass_only() {
        assert!(is_our_game("US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET", "Northeastern"));
        assert!(is_our_game("UMass Lowell vs. UMass", "UMass Lowell"));
        assert!(!is_our_game("UMass Lowell vs. Maine", "Maine"));
        assert!(!is_our_game("UMass-Lowell at Maine", "Maine"));
        assert!(!is_our_game("UMass Football vs Maine", "Maine"));
        assert!(!is_our_game("UMass vs Maine Women", "Maine"));
        assert!(is_our_game("BU at UMass", "Boston University"));
        assert!(!is_our_game("BU at UMass Boston", "Boston University"));
        assert!(!is_our_game("UMass vs Vermont", "Maine"));
        assert!(is_our_game("UMass vs TBD", "TBD"));
    }

    #[test]
    fn finds_dates_in_names() {
        assert_eq!(date_in("Massachusetts Oct 17 5:30PM", 2026), NaiveDate::from_ymd_opt(2026, 10, 17));
        assert_eq!(date_in("UMass 17 Oct", 2026), NaiveDate::from_ymd_opt(2026, 10, 17));
        assert_eq!(date_in("UMass (2026-11-07 19:00)", 2026), NaiveDate::from_ymd_opt(2026, 11, 7));
        assert_eq!(date_in("UMass October 3", 2026), NaiveDate::from_ymd_opt(2026, 10, 3));
        assert_eq!(date_in("UMass vs Maine", 2026), None);
    }

    #[test]
    fn puck_drop_in_local_time() {
        let eastern = |g: &Game| start(g).map(|t| {
            Local.from_local_datetime(&t).unwrap().with_timezone(&chrono_tz::America::New_York).format("%Y-%m-%d %H:%M").to_string()
        });
        let g = game("2026-11-07", "at", "Maine", "7:00 ET", "", "", "");
        assert_eq!(eastern(&g).as_deref(), Some("2026-11-07 19:00"));
        let g = game("2026-12-29", "vs.", "Arizona State (nc)", "4:00 CT", "", "", "");
        assert_eq!(eastern(&g).as_deref(), Some("2026-12-29 17:00"));
        let g = game("2026-12-29", "", "X", "12:30 ET", "", "", "");
        assert_eq!(eastern(&g).as_deref(), Some("2026-12-29 12:30"));
        assert_eq!(start(&game("2026-12-30", "", "X", "", "", "", "")), None);
    }

    #[test]
    fn unescapes_entities() {
        assert_eq!(unescape("a &amp; b &#39;c&#x27; &ndash;&nbsp;"), "a & b 'c' –\u{a0}");
        assert_eq!(cell("<b> 02&nbsp;Fri\t</b>"), "02 Fri");
    }
}
