//! The QMJHL (LHJMQ) players we follow: their team's schedule, their own stats, and where we can watch each game.
//!
//! Justin Gagnon (#20, Rimouski Océanic) is the son of a friend. The league publishes
//! everything through its HockeyTech feed (the one its own site and app read): the team
//! schedule with times and results, and each player's season and game-by-game stats.
//! `kdtv qmjhl --refresh` saves it three times a day; JP's qmjhl_games tool reads that
//! file plus our channel list, where FloHockey carries every game as an event channel
//! ("(FLSP 474) | hockey:  Drummondville Voltigeurs vs Rimouski Oceanic (Home) (2026-10-30 19:00:10)").
//! Following someone else in the league is one more line in PLAYERS.

use crate::config::Config;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Local, NaiveDate, NaiveDateTime, TimeZone};
use regex::Regex;
use rusqlite::{Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::LazyLock;

const FEED: &str = "https://lscluster.hockeytech.com/feed/?feed=modulekit&key=f322673b6bcae299&client_code=lhjmq&fmt=json";
const WATCH_URL: &str = "https://www.flohockey.tv/leagues/10826800-ligue-de-hockey-junior-maritimes-quebec-lhjmq";

struct Player {
    name: &'static str,
    /// HockeyTech ids: the player's and his team's.
    player_id: u32,
    team_id: u32,
    about: &'static str,
}

const PLAYERS: &[Player] = &[Player {
    name: "Justin Gagnon",
    player_id: 24253,
    team_id: 18,
    about: "#20, right wing, rookie (born 2010-02-06 in Rimouski, 6'4\"), Rimouski's 1st-round pick (8th overall) in the 2026 QMJHL draft",
}];

#[derive(Serialize, Deserialize, Clone, PartialEq, Debug, Default)]
struct Game {
    id: String,
    date: String,
    /// Puck drop as the league publishes it (RFC 3339, with the arena's offset); empty when not set yet.
    #[serde(default)]
    start: String,
    #[serde(rename = "where")]
    place: String,
    opponent: String,
    #[serde(default)]
    opponent_city: String,
    #[serde(default)]
    opponent_nickname: String,
    #[serde(default)]
    venue: String,
    #[serde(default)]
    result: String,
    #[serde(default)]
    on_flohockey: bool,
    #[serde(default)]
    radio: String,
}

#[derive(Serialize, Deserialize, Clone, Default)]
struct GameLine {
    goals: i64,
    assists: i64,
    plus_minus: i64,
    penalty_minutes: f64,
    shots: i64,
}

#[derive(Serialize, Deserialize, Default)]
struct PlayerFile {
    fetched_at: Option<i64>,
    season: String,
    team: String,
    /// The season so far: games played, goals, assists, points, +/-, penalty minutes.
    #[serde(default)]
    stats: Value,
    games: Vec<Game>,
    /// His line in each game he played, by game id.
    #[serde(default)]
    lines: HashMap<String, GameLine>,
}

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).expect("valid regex")
}

fn player_file(cfg: &Config, p: &Player) -> PathBuf {
    cfg.data_dir.join("qmjhl").join(format!("{}.json", p.player_id))
}

/// The player JP asked about (any part of the name, accents and case ignored); the first one when none is given.
fn find_player(name: &str) -> Option<&'static Player> {
    let wanted = fold(name.trim());
    if wanted.is_empty() {
        return PLAYERS.first();
    }
    PLAYERS.iter().find(|p| fold(p.name).split(' ').any(|part| wanted.split(' ').any(|w| w == part)))
}

/// Lower case without accents, so "Océanic", "Oceanic" and "OCEANIC" all match.
fn fold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.to_lowercase().chars() {
        match c {
            'à' | 'â' | 'ä' => out.push('a'),
            'ç' => out.push('c'),
            'é' | 'è' | 'ê' | 'ë' => out.push('e'),
            'î' | 'ï' => out.push('i'),
            'ô' | 'ö' => out.push('o'),
            'œ' => out.push_str("oe"),
            'ù' | 'û' | 'ü' => out.push('u'),
            _ => out.push(c),
        }
    }
    out
}

// ── the refresh (`kdtv qmjhl --refresh`, three times a day) ───────────────

fn fetch(params: &str) -> Result<Value> {
    let url = format!("{FEED}&{params}");
    let out = Command::new("curl")
        .args(["-sS", "--fail", "-L", "--max-time", "30", "-A", "Mozilla/5.0 (KD CLI; JP)", &url])
        .output()
        .context("running curl")?;
    if !out.status.success() {
        bail!("LHJMQ feed: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    serde_json::from_slice::<Value>(&out.stdout).context("LHJMQ feed: not JSON")
}

fn text(v: &Value) -> String {
    match v {
        Value::String(s) => s.trim().to_string(),
        Value::Number(n) => n.to_string(),
        _ => String::new(),
    }
}

fn int(v: &Value) -> i64 {
    text(v).parse::<f64>().map(|f| f as i64).unwrap_or(0)
}

/// One row of the league's team schedule, seen from our team's side.
fn parse_game(g: &Value, team_id: u32) -> Option<Game> {
    let home = text(&g["home_team"]) == team_id.to_string();
    if !home && text(&g["visiting_team"]) != team_id.to_string() {
        return None;
    }
    let (us, them) = if home { ("home", "visiting") } else { ("visiting", "home") };
    let date = text(&g["date_played"]);
    NaiveDate::parse_from_str(&date, "%Y-%m-%d").ok()?;
    let result = if text(&g["final"]) == "1" {
        let (ours, theirs) = (int(&g[format!("{us}_goal_count")]), int(&g[format!("{them}_goal_count")]));
        let extra = if text(&g["shootout"]) == "1" {
            " SO"
        } else if text(&g["overtime"]) == "1" {
            " OT"
        } else {
            ""
        };
        format!("{} {ours}-{theirs}{extra}", if ours > theirs { "W" } else { "L" })
    } else {
        String::new()
    };
    let on_flohockey = g["broadcasters"].as_object().is_some_and(|b| {
        b.values().filter_map(Value::as_array).flatten().any(|x| fold(&text(&x["name"])).contains("flohockey"))
    });
    let city = text(&g[format!("{them}_team_city")]);
    let nickname = text(&g[format!("{them}_team_nickname")]);
    Some(Game {
        id: text(&g["game_id"]),
        date,
        start: if text(&g["time_tbd"]) == "1" { String::new() } else { text(&g["GameDateISO8601"]) },
        place: if home { "home" } else { "away" }.into(),
        opponent: format!("{city} {nickname}").trim().to_string(),
        opponent_city: city,
        opponent_nickname: nickname,
        venue: [text(&g["venue_name"]), text(&g["venue_location"])].iter().filter(|s| !s.is_empty()).cloned().collect::<Vec<_>>().join(", "),
        result,
        on_flohockey,
        radio: text(&g[format!("{us}_audio_url")]),
    })
}

fn parse_lines(gamebygame: &Value) -> HashMap<String, GameLine> {
    let games = gamebygame["SiteKit"]["Player"]["games"].as_array().cloned().unwrap_or_default();
    games
        .iter()
        .map(|g| {
            let line = GameLine {
                goals: int(&g["goals"]),
                assists: int(&g["assists"]),
                plus_minus: int(&g["plus_minus"]),
                penalty_minutes: text(&g["penalty_minutes"]).parse().unwrap_or(0.0),
                shots: int(&g["shots"]),
            };
            (text(&g["id"]), line)
        })
        .collect()
}

fn parse_stats(seasonstats: &Value, season: &str) -> Value {
    let rows = seasonstats["SiteKit"]["Player"].as_object().into_iter().flat_map(|m| m.values()).filter_map(Value::as_array).flatten();
    let Some(row) = rows.into_iter().find(|r| text(&r["season_id"]) == season) else { return Value::Null };
    json!({
        "games_played": int(&row["games_played"]), "goals": int(&row["goals"]), "assists": int(&row["assists"]),
        "points": int(&row["points"]), "plus_minus": int(&row["plus_minus"]), "penalty_minutes": int(&row["penalty_minutes"]),
        "power_play_goals": int(&row["power_play_goals"]), "shots": int(&row["shots"]),
    })
}

/// Download each player's schedule and stats; keep the last good copy when the feed looks wrong.
pub fn refresh(cfg: &Config) -> Result<Value> {
    let seasons = fetch("view=seasons")?;
    let season = text(&seasons["SiteKit"]["Parameters"]["season_id"]);
    if season.is_empty() {
        bail!("LHJMQ feed: no current season");
    }
    let season_name = seasons["SiteKit"]["Seasons"]
        .as_array()
        .and_then(|list| list.iter().find(|s| text(&s["season_id"]) == season))
        .map(|s| text(&s["season_name"]))
        .unwrap_or_default();
    let mut done = vec![];
    for p in PLAYERS {
        let schedule = fetch(&format!("view=schedule&team_id={}&season_id={season}", p.team_id))?;
        let rows = schedule["SiteKit"]["Schedule"].as_array().cloned().unwrap_or_default();
        let games: Vec<Game> = rows.iter().filter_map(|g| parse_game(g, p.team_id)).collect();
        if games.len() < 10 {
            done.push(json!({ "player": p.name, "ok": false, "error": format!("only {} games parsed; kept the previous schedule", games.len()) }));
            continue;
        }
        let team = rows
            .iter()
            .find_map(|g| {
                ["home", "visiting"]
                    .into_iter()
                    .find(|side| text(&g[format!("{side}_team")]) == p.team_id.to_string())
                    .map(|side| format!("{} {}", text(&g[format!("{side}_team_city")]), text(&g[format!("{side}_team_nickname")])))
            })
            .unwrap_or_default();
        // His stats are a bonus: the schedule is still worth saving without them.
        let stats = fetch(&format!("view=player&category=seasonstats&player_id={}&season_id={season}", p.player_id))
            .map(|v| parse_stats(&v, &season))
            .unwrap_or(Value::Null);
        let lines = fetch(&format!("view=player&category=gamebygame&player_id={}&season_id={season}", p.player_id))
            .map(|v| parse_lines(&v))
            .unwrap_or_default();
        let count = games.len();
        let file = PlayerFile { fetched_at: Some(chrono::Utc::now().timestamp()), season: season_name.clone(), team, stats, games, lines };
        write_file(&player_file(cfg, p), &file)?;
        done.push(json!({ "player": p.name, "ok": true, "games": count, "record": record(&file.games) }));
    }
    let ok = done.iter().all(|d| d["ok"] == true);
    Ok(json!({ "ok": ok, "players": done }))
}

fn write_file(path: &Path, file: &PlayerFile) -> Result<()> {
    std::fs::create_dir_all(path.parent().unwrap_or(Path::new(".")))?;
    let mut out = vec![];
    let mut ser = serde_json::Serializer::with_formatter(&mut out, serde_json::ser::PrettyFormatter::with_indent(b" "));
    file.serialize(&mut ser)?;
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, out)?;
    std::fs::rename(&tmp, path).with_context(|| format!("writing {}", path.display()))
}

// ── JP's look-up ──────────────────────────────────────────────────────────

/// Wins, losses, overtime losses, shootout losses: the way the QMJHL counts a record.
fn record(games: &[Game]) -> String {
    let (mut w, mut l, mut otl, mut sol) = (0, 0, 0, 0);
    for g in games.iter().filter(|g| !g.result.is_empty()) {
        match (g.result.starts_with('W'), g.result.ends_with(" SO"), g.result.ends_with(" OT")) {
            (true, _, _) => w += 1,
            (false, true, _) => sol += 1,
            (false, _, true) => otl += 1,
            _ => l += 1,
        }
    }
    if w + l + otl + sol == 0 {
        return String::new();
    }
    format!("{w}-{l}-{otl}-{sol} (W-L-OTL-SOL)")
}

/// Puck drop in local time, or None when no time is published yet.
fn start(g: &Game) -> Option<NaiveDateTime> {
    DateTime::parse_from_rfc3339(&g.start).ok().map(|t| t.with_timezone(&Local).naive_local())
}

fn line_text(l: &GameLine) -> String {
    let points = l.goals + l.assists;
    let mut text = format!("{} G, {} A ({points} pt{}), {:+}", l.goals, l.assists, if points == 1 { "" } else { "s" }, l.plus_minus);
    if l.penalty_minutes > 0.0 {
        text.push_str(&format!(", {} PIM", l.penalty_minutes));
    }
    text
}

/// A date written in an event channel name: "(2026-10-30 19:00:10)".
fn iso_date_in(text: &str) -> Option<NaiveDate> {
    static ISO: LazyLock<Regex> = LazyLock::new(|| re(r"\b(20\d\d-\d\d-\d\d)\b"));
    ISO.captures(text).and_then(|c| NaiveDate::parse_from_str(&c[1], "%Y-%m-%d").ok())
}

/// Is this channel name (FloHockey event channel) our team's game against this opponent, that day?
fn is_our_game(name: &str, team: &str, g: &Game) -> bool {
    let name = fold(name);
    let ours = fold(team).split(' ').any(|w| w.len() > 3 && name.contains(w));
    let theirs = [&g.opponent_nickname, &g.opponent_city].iter().map(|s| fold(s)).any(|w| !w.is_empty() && name.contains(&w));
    ours && theirs && iso_date_in(&name).map(|d| d.format("%Y-%m-%d").to_string()) == Some(g.date.clone())
}

/// Whose broadcast an event channel is: Flo offers the home team's feed and the visitors' feed.
fn feed_of(name: &str, g: &Game) -> &'static str {
    let side = if name.contains("(Home)") {
        "home"
    } else if name.contains("(Away)") {
        "away"
    } else {
        return "";
    };
    if side == g.place { "our team's own broadcast" } else { "the other team's broadcast" }
}

/// Our channels carrying each of these games (FloHockey event channels, LHJMQ guide slots), keyed by game id.
fn channels_for(cfg: &Config, team: &str, games: &[&Game]) -> HashMap<String, Vec<Value>> {
    let mut found: HashMap<String, Vec<Value>> = games.iter().map(|g| (g.id.clone(), vec![])).collect();
    let Ok(conn) = Connection::open_with_flags(cfg.data_dir.join("iptv.db"), OpenFlags::SQLITE_OPEN_READ_ONLY) else {
        return found;
    };
    let city = team.split(' ').next().unwrap_or(team).to_string();
    let nickname = team.rsplit(' ').next().unwrap_or(team).to_string();
    let names: Vec<(i64, String, Option<String>)> = conn
        .prepare("SELECT stream_id, name, category_name FROM live_streams WHERE name LIKE ?1 OR name LIKE ?2 OR name LIKE ?3")
        .and_then(|mut s| {
            s.query_map([format!("%{city}%"), format!("%{nickname}%"), format!("%{}%", fold(&nickname))], |r| {
                Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default(), r.get(2)?))
            })?
            .collect()
        })
        .unwrap_or_default();
    let since = games.iter().filter_map(|g| start(g)).min().map(|t| local_ts(t) - 2 * 3600).unwrap_or(0);
    let guide: Vec<(i64, String, String, i64)> = conn
        .prepare(
            "SELECT e.stream_id, l.name, e.title, e.start_time FROM epg e JOIN live_streams l USING(stream_id) \
             WHERE e.end_time > ? AND (e.title LIKE '%LHJMQ%' OR e.title LIKE '%QMJHL%')",
        )
        .and_then(|mut s| {
            s.query_map([since], |r| {
                Ok((r.get(0)?, r.get::<_, Option<String>>(1)?.unwrap_or_default(), r.get::<_, Option<String>>(2)?.unwrap_or_default(), r.get(3)?))
            })?
            .collect()
        })
        .unwrap_or_default();
    for g in games {
        let list = found.entry(g.id.clone()).or_default();
        let mut flo: Vec<Value> = names
            .iter()
            .filter(|(_, name, _)| is_our_game(name, team, g))
            .map(|(stream_id, name, category)| {
                let mut item = json!({ "stream_id": stream_id, "channel": name, "category": category, "how": "FloHockey event channel" });
                let feed = feed_of(name, g);
                if !feed.is_empty() {
                    item["feed"] = feed.into();
                }
                item
            })
            .collect();
        flo.sort_by_key(|c| c["feed"] != "our team's own broadcast");
        list.extend(flo);
        // TV (TVA Sports) only says "Hockey LHJMQ": a slot starting near puck drop may be this game, or another one.
        if let Some(puck) = start(g).map(local_ts) {
            for (stream_id, name, title, start_time) in &guide {
                if (*start_time - puck).abs() <= 45 * 60 {
                    let at = Local.timestamp_opt(*start_time, 0).single().map(|t| t.format("%H:%M").to_string()).unwrap_or_default();
                    list.push(json!({ "stream_id": stream_id, "channel": name, "how": format!("guide: {title} at {at} (the guide doesn't name the teams: maybe another game)") }));
                }
            }
        }
        list.truncate(6);
    }
    found
}

fn local_ts(t: NaiveDateTime) -> i64 {
    Local.from_local_datetime(&t).earliest().map(|d: DateTime<Local>| d.timestamp()).unwrap_or_else(|| t.and_utc().timestamp())
}

/// Next games (with the channels we have for any in the next 3 days), the latest results with his line, his stats.
pub fn games(cfg: &Config, player: &str, upcoming: usize, past: usize) -> Value {
    games_at(cfg, player, Local::now().naive_local(), upcoming, past)
}

fn games_at(cfg: &Config, player: &str, now: NaiveDateTime, upcoming: usize, past: usize) -> Value {
    let Some(p) = find_player(player) else {
        let names: Vec<&str> = PLAYERS.iter().map(|p| p.name).collect();
        return json!({ "error": format!("we don't follow {player:?}; we follow: {}", names.join(", ")) });
    };
    let Some(data) = std::fs::read_to_string(player_file(cfg, p)).ok().and_then(|t| serde_json::from_str::<PlayerFile>(&t).ok()) else {
        return json!({ "player": p.name, "error": "his schedule isn't downloaded yet (kdtv qmjhl --refresh)", "official_streams": WATCH_URL });
    };
    let end = |g: &Game| match start(g) {
        Some(s) => s + Duration::hours(3),
        None => NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").map(|d| d.and_hms_opt(0, 0, 0).unwrap_or_default() + Duration::days(1)).unwrap_or(now),
    };
    let next: Vec<&Game> = data.games.iter().filter(|g| g.result.is_empty() && end(g) >= now).take(upcoming.clamp(1, 10)).collect();
    let horizon = now.date() + Duration::days(3);
    let soon: Vec<&Game> = next.iter().copied().filter(|g| NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").is_ok_and(|d| d <= horizon)).collect();
    let channels = channels_for(cfg, &data.team, &soon);

    let next_games: Vec<Value> = next
        .iter()
        .map(|g| {
            let local_start = match start(g) {
                Some(s) => s.format("%a %Y-%m-%d %H:%M").to_string(),
                None => {
                    let day = NaiveDate::parse_from_str(&g.date, "%Y-%m-%d").map(|d| d.format("%a %Y-%m-%d").to_string()).unwrap_or_default();
                    format!("{day}, time not published yet")
                }
            };
            let ours: Value = if soon.iter().any(|x| std::ptr::eq(*x, *g)) {
                match channels.get(&g.id) {
                    Some(list) if !list.is_empty() => Value::Array(list.clone()),
                    _ => "none found yet".into(),
                }
            } else {
                "not checked (event channels only appear a day or two before the game)".into()
            };
            let mut item = json!({ "local_start": local_start, "where": g.place, "opponent": g.opponent, "venue": g.venue, "our_channels": ours });
            if g.on_flohockey {
                item["official_stream"] = "FloHockey".into();
            }
            if !g.radio.is_empty() {
                item["radio"] = g.radio.clone().into();
            }
            item
        })
        .collect();

    let finished: Vec<&Game> = data.games.iter().filter(|g| !g.result.is_empty()).collect();
    let latest: Vec<Value> = finished[finished.len().saturating_sub(past.min(10))..]
        .iter()
        .map(|g| {
            let his = data.lines.get(&g.id).map(line_text).unwrap_or_else(|| "did not play (not in his game log)".into());
            json!({ "date": g.date, "where": g.place, "opponent": g.opponent, "result": g.result, p.name: his })
        })
        .collect();
    let age_hours = data.fetched_at.map(|f| ((chrono::Utc::now().timestamp() - f) as f64 / 3600.0 * 10.0).round() / 10.0);
    json!({
        "player": p.name,
        "about": p.about,
        "team": format!("{} (QMJHL / LHJMQ), {}", data.team, data.season),
        "team_record": record(&data.games),
        "his_season": data.stats,
        "next_games": next_games,
        "latest_results": latest,
        "schedule_age_hours": age_hours,
        "channel_list_age_minutes": crate::status::list_age_minutes(&cfg.data_dir).0,
        "official_streams": format!("FloHockey streams every LHJMQ game (paid): {WATCH_URL}. TVA Sports shows some games. Our channel list carries the FloHockey games as event channels a day or two before."),
        "times": "local_start is already in local time (Eastern).",
        "result": "result is the team's score first: W 4-2 = won 4 to 2; OT / SO = decided in overtime / shootout.",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn feed_row(home: &str, visiting: &str, extra: Value) -> Value {
        let mut g = json!({
            "game_id": "32700", "date_played": "2026-10-30", "GameDateISO8601": "2026-10-30T19:00:00-04:00", "time_tbd": "0",
            "home_team": home, "visiting_team": visiting, "final": "0", "overtime": "0", "shootout": "0",
            "home_goal_count": "0", "visiting_goal_count": "0",
            "home_team_city": "Rimouski", "home_team_nickname": "Océanic",
            "visiting_team_city": "Drummondville", "visiting_team_nickname": "Voltigeurs",
            "venue_name": "Colisée Financière Sun Life", "venue_location": "Rimouski (QC)",
            "home_audio_url": "https://www.cfyxrimouski.com/", "visiting_audio_url": "",
            "broadcasters": { "home_webcast": [{ "name": "FLOHOCKEY" }] },
        });
        if let (Some(g), Value::Object(extra)) = (g.as_object_mut(), extra) {
            g.extend(extra);
        }
        g
    }

    #[test]
    fn reads_a_game_from_our_side() {
        let g = parse_game(&feed_row("18", "14", json!({})), 18).unwrap();
        assert_eq!((g.place.as_str(), g.opponent.as_str(), g.result.as_str()), ("home", "Drummondville Voltigeurs", ""));
        assert!(g.on_flohockey);
        assert_eq!(g.radio, "https://www.cfyxrimouski.com/");
        assert_eq!(start(&g).map(local_ts), DateTime::parse_from_rfc3339("2026-10-30T23:00:00Z").ok().map(|t| t.timestamp()));

        let away = json!({ "home_team_city": "Québec", "home_team_nickname": "Remparts", "visiting_team_city": "Rimouski",
            "visiting_team_nickname": "Océanic", "final": "1", "overtime": "1", "home_goal_count": "5", "visiting_goal_count": "4" });
        let g = parse_game(&feed_row("9", "18", away), 18).unwrap();
        assert_eq!((g.place.as_str(), g.opponent.as_str(), g.result.as_str()), ("away", "Québec Remparts", "L 4-5 OT"));
        assert!(parse_game(&feed_row("9", "14", json!({})), 18).is_none());
    }

    #[test]
    fn counts_the_record_like_the_league() {
        let r = |s: &str| Game { result: s.into(), ..Default::default() };
        let games = [r("W 4-2"), r("L 4-5 OT"), r("W 4-3 SO"), r("L 3-4 SO"), r("L 1-3"), r("")];
        assert_eq!(record(&games), "2-1-1-1 (W-L-OTL-SOL)");
        assert_eq!(record(&[]), "");
    }

    #[test]
    fn spots_our_flohockey_channels() {
        let g = parse_game(&feed_row("18", "14", json!({})), 18).unwrap();
        let team = "Rimouski Océanic";
        let home = "(FLSP 474) | hockey:  Drummondville Voltigeurs vs Rimouski Oceanic (Home) (2026-10-30 19:00:10)";
        let away = "(FLSP 475) | hockey:  Drummondville Voltigeurs vs Rimouski Oceanic (Away) (2026-10-30 19:00:15)";
        assert!(is_our_game(home, team, &g));
        assert_eq!(feed_of(home, &g), "our team's own broadcast");
        assert_eq!(feed_of(away, &g), "the other team's broadcast");
        assert!(!is_our_game("(FLSP 485) | hockey:  Chicoutimi Sagueneens vs Sherbrooke Phoenix (Home) (2026-10-30 19:01:05)", team, &g));
        assert!(!is_our_game("(FLSP 400) | hockey:  Drummondville Voltigeurs vs Rimouski Oceanic (Home) (2026-11-30 19:00:10)", team, &g));
        assert!(is_our_game("US (Flo 481) | [Hockey|2026 Drummondville Voltigeurs vs Rimouski Oceanic Home] (2026-10-30 19:00:00)", team, &g));
    }

    #[test]
    fn finds_the_player_by_any_name() {
        assert_eq!(find_player("").map(|p| p.name), Some("Justin Gagnon"));
        assert_eq!(find_player("justin").map(|p| p.name), Some("Justin Gagnon"));
        assert_eq!(find_player("Gagnon").map(|p| p.name), Some("Justin Gagnon"));
        assert!(find_player("Maxime").is_none());
    }

    #[test]
    fn writes_his_line() {
        let l = GameLine { goals: 1, assists: 0, plus_minus: -1, penalty_minutes: 2.0, shots: 3 };
        assert_eq!(line_text(&l), "1 G, 0 A (1 pt), -1, 2 PIM");
        assert_eq!(fold("Phœnix Océanic"), "phoenix oceanic");
    }
}
