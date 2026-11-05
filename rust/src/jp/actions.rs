//! JP's look-ups and proposals. Look-ups answer directly; actions (record, delete,
//! pause, restream, rebuild, rescan) only write a *pending* action in
//! `data/agent/pending/`. Nothing happens until someone taps Confirm on the OMT
//! page, which reaches `execute` through the runner. The model never executes
//! anything itself.

use super::card;
use crate::config::Config;
use crate::{db, notify, refresh, restream, runs, schedules, status};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

/// A proposal not confirmed within half an hour is dropped.
const PENDING_TTL_SECONDS: f64 = 30.0 * 60.0;
/// Family can do everything the owner can with recordings and the restream.
const FAMILY_ACTIONS: &[&str] = &["create_schedule", "delete_schedule", "pause_schedule", "start_restream", "stop_restream"];

fn agent_dir(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("agent")
}

fn pending_dir(cfg: &Config) -> PathBuf {
    agent_dir(cfg).join("pending")
}

fn history_file(cfg: &Config) -> PathBuf {
    agent_dir(cfg).join("history.jsonl")
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

fn error(message: impl std::fmt::Display) -> Value {
    json!({ "error": message.to_string() })
}

// ── look-ups ─────────────────────────────────────────────────────────────

/// Matches plus how old the channel list is, so JP knows when to refresh first.
pub fn find_channel(cfg: &Config, query: &str) -> Value {
    let channels = match db::Db::open(&cfg.data_dir).and_then(|d| d.search_channels(query, 8)) {
        Ok(c) => c,
        Err(e) => return error(e),
    };
    let (list_age, guide_age) = status::list_age_minutes(&cfg.data_dir);
    json!({ "channels": channels, "list_age_minutes": list_age, "guide_age_minutes": guide_age })
}

/// Download the channel list now (about 5 s), plus the full guide if asked (about
/// 25 s more). A look-up, not an action: it changes no recording or restream.
pub fn refresh_channels(cfg: &Config, include_guide: bool) -> Value {
    let (list_age, _) = status::list_age_minutes(&cfg.data_dir);
    // A timer or another chat may have just done it.
    let result = if !include_guide && list_age.is_some_and(|a| a < 2) {
        Ok(format!("already fresh ({} min old)", list_age.unwrap_or(0)))
    } else {
        refresh::run(cfg, refresh::What { guide: include_guide, everything: false })
    };
    let (list_age, guide_age) = status::list_age_minutes(&cfg.data_dir);
    match result {
        Ok(message) => json!({ "ok": true, "message": message, "list_age_minutes": list_age, "guide_age_minutes": guide_age }),
        Err(e) => json!({ "ok": false, "message": format!("{e:#}"), "list_age_minutes": list_age, "guide_age_minutes": guide_age }),
    }
}

/// Now and the next three shows.
pub fn whats_on(cfg: &Config, stream_id: i64) -> Value {
    let programs = match db::Db::open(&cfg.data_dir).and_then(|d| d.guide(stream_id, chrono::Utc::now().timestamp(), 4)) {
        Ok(p) => p,
        Err(e) => return error(e),
    };
    if programs.is_empty() {
        return json!({ "programs": [], "note": "No guide information for this channel." });
    }
    json!({ "programs": programs })
}

/// Movies and series the provider has, in the family's languages unless asked.
/// Titles are "QC - Dune (2021)", "4K-FR - Dune (2021)", "FR - Breaking Bad".
pub fn find_title(cfg: &Config, query: &str, language: &str) -> Value {
    const SHOWN: usize = 8;
    let db = match db::Db::open(&cfg.data_dir) {
        Ok(d) => d,
        Err(e) => return error(e),
    };
    let (Ok(movies), Ok(series)) = (db.search_movies(query, 2000), db.search_series(query, 2000)) else {
        return error("couldn't read the movie and series list");
    };
    let languages: Vec<String> = match language.trim().to_uppercase().as_str() {
        "" => vec!["QC".into(), "FR".into(), "EN".into()],
        "ALL" => vec![],
        l => l.split([',', ' ']).filter(|c| !c.is_empty()).map(str::to_string).collect(),
    };
    let wanted = |t: &Title| languages.is_empty() || languages.contains(&t.language);
    // Exact title first, then titles starting with it, then the rest; QC before FR before EN.
    let q = query.trim().to_lowercase();
    let rank = |t: &Title| {
        let title = t.title.to_lowercase();
        let fit = if title == q { 0 } else if title.starts_with(&q) { 1 } else { 2 };
        (fit, languages.iter().position(|l| *l == t.language).unwrap_or(99), title)
    };

    let mut found_movies: Vec<(Title, &db::Movie)> = movies.iter().map(|m| (Title::parse(&m.name), m)).filter(|(t, _)| wanted(t)).collect();
    found_movies.sort_by_key(|(t, _)| rank(t));
    let mut found_series: Vec<(Title, &db::Series)> = series.iter().map(|s| (Title::parse(&s.name), s)).filter(|(t, _)| wanted(t)).collect();
    found_series.sort_by_key(|(t, _)| rank(t));

    let movie_rows: Vec<Value> = found_movies.iter().take(SHOWN).map(|(t, m)| json!({
        "title": t.title, "language": t.language, "4k": t.uhd,
        "year": m.year.clone().or_else(|| t.year.clone()), "category": m.category, "genre": m.genre, "rating": m.rating,
    })).collect();
    // Seasons and episodes only for the top few: each one not seen before is a provider call.
    let series_rows: Vec<Value> = found_series.iter().take(SHOWN).enumerate().map(|(i, (t, s))| {
        let mut row = json!({ "title": t.title, "language": t.language, "category": s.category, "genre": s.genre, "rating": s.rating });
        if i < 3 {
            row["plot"] = json!(s.plot.as_ref().map(|p| p.chars().take(200).collect::<String>()));
            if let Some((seasons, episodes)) = season_count(cfg, &db, s.series_id) {
                row["seasons"] = json!(seasons);
                row["episodes"] = json!(episodes);
            }
        }
        row
    }).collect();
    json!({
        "movies": movie_rows, "movies_found": found_movies.len(),
        "series": series_rows, "series_found": found_series.len(),
        "languages": if languages.is_empty() { json!("all") } else { json!(languages) },
        "list_age_days": status::catalog_age_minutes(&cfg.data_dir).map(|m| m / (24 * 60)),
    })
}

/// (seasons, episodes) from the saved episode list, fetching it once if needed.
fn season_count(cfg: &Config, db: &db::Db, series_id: i64) -> Option<(usize, usize)> {
    let mut episodes = db.episodes(series_id).ok()?;
    if episodes.is_empty() {
        crate::provider::fetch_episodes(cfg, series_id).ok()?;
        episodes = db.episodes(series_id).ok()?;
    }
    let mut seasons: Vec<i64> = episodes.iter().map(|e| e.season).collect();
    seasons.dedup();
    Some((seasons.len(), episodes.len()))
}

/// "4K-FR - Dune  (2021)" → title "Dune", language "FR", 4K, year 2021.
struct Title {
    title: String,
    language: String,
    uhd: bool,
    year: Option<String>,
}

impl Title {
    fn parse(name: &str) -> Title {
        let (uhd, rest) = match name.strip_prefix("4K-") {
            Some(r) => (true, r),
            None => (false, name),
        };
        let (language, rest) = match rest.split_once(" - ") {
            Some((code, r)) if code.len() <= 4 && code.chars().all(|c| c.is_ascii_uppercase()) => (code.to_string(), r),
            _ => (String::new(), rest),
        };
        let mut title = rest.trim().to_string();
        let mut year = None;
        if let Some((before, after)) = title.rsplit_once('(') {
            let y = after.trim_end_matches(')');
            if y.len() == 4 && y.chars().all(|c| c.is_ascii_digit()) {
                year = Some(y.to_string());
                title = before.trim().to_string();
            }
        }
        Title { title, language, uhd, year }
    }
}

pub fn list_schedules(cfg: &Config) -> Value {
    json!(schedules::list(&cfg.data_dir))
}

pub fn recent_recordings(cfg: &Config) -> Value {
    json!(runs::recent(&cfg.data_dir, 10))
}

pub fn restream_status(cfg: &Config) -> Value {
    match restream::active(&cfg.data_dir) {
        Some(a) => json!({ "live": true, "channel_name": a.channel_name, "since": a.started_at, "watch_at": cfg.watch_url }),
        None => json!({ "live": false }),
    }
}

// ── proposals (pending until confirmed) ──────────────────────────────────

pub struct Who<'a> {
    pub session: &'a str,
    pub user: Option<&'a str>,
}

/// `summary` is the one-line record for logs; `card` is what the Confirm card shows (card.rs).
fn propose(cfg: &Config, who: &Who, kind: &str, args: Value, summary: String, card: Value) -> Value {
    let id = crate::jp::random_hex(6);
    let item = json!({ "id": id, "session": who.session, "kind": kind, "args": args, "summary": summary,
                       "card": card, "user": who.user, "created": now() });
    let dir = pending_dir(cfg);
    if let Err(e) = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(dir.join(format!("{id}.json")), item.to_string())) {
        return error(format!("could not save the proposal: {e}"));
    }
    json!({ "pending_id": id, "summary": summary,
            "note": "Shown to the owner as a card with Confirm / Cancel. Nothing has happened yet." })
}

pub struct Recording<'a> {
    pub stream_id: i64,
    pub channel_name: &'a str,
    pub time: &'a str,
    pub duration_minutes: i64,
    pub days: Vec<String>,
    pub date: Option<String>,
    pub show_name: Option<&'a str>,
    pub notify: bool,
}

pub fn propose_recording(cfg: &Config, who: &Who, r: Recording) -> Value {
    let new = schedules::New {
        stream_id: r.stream_id,
        channel_name: r.channel_name,
        time: r.time,
        duration_min: r.duration_minutes,
        days: r.days.clone(),
        date: if r.days.is_empty() { r.date.clone() } else { None },
        show_name: r.show_name.filter(|s| !s.trim().is_empty()),
        notify: r.notify,
        created_by: who.user.unwrap_or("agent"),
    };
    let errors = schedules::validate(&new);
    if !errors.is_empty() {
        return error(errors.join("; "));
    }
    // Say it now rather than after the Confirm tap.
    if let Some(clash) = schedules::clash(&cfg.data_dir, &new) {
        return error(format!("this overlaps “{clash}”; the provider allows one connection, so only one recording can run at a time"));
    }
    let mut summary = format!("Record {}", schedules::describe_new(&new));
    if r.notify || new.show_name.is_some() {
        summary += " · Discord ping";
    }
    if let Some(u) = who.user {
        summary += &format!(" · for {u}");
    }
    let args = json!({ "stream_id": r.stream_id, "channel_name": r.channel_name, "time": r.time,
                       "duration_min": r.duration_minutes, "days": r.days, "date": new.date,
                       "show_name": new.show_name, "notify": r.notify, "for": who.user });
    let card = card::recording(&card::Rec {
        channel: r.channel_name,
        time: r.time,
        minutes: r.duration_minutes,
        days: &r.days,
        date: new.date.as_deref(),
        show: new.show_name,
        notify: r.notify,
    });
    propose(cfg, who, "create_schedule", args, summary, card)
}

/// The card for changing schedule `id`, or None when there is no such schedule.
fn schedule_card(cfg: &Config, id: &str, what: &str) -> Option<(String, Value)> {
    let s = schedules::load(&cfg.data_dir, id)?;
    let card = card::schedule_change(
        &card::Rec {
            channel: &s.channel_name,
            time: &s.time,
            minutes: s.duration_min,
            days: &s.days,
            date: s.date.as_deref(),
            show: s.show_name.as_deref(),
            notify: s.notify,
        },
        what,
    );
    Some((schedules::describe_id(&cfg.data_dir, id).unwrap_or_default(), card))
}

pub fn propose_delete_schedule(cfg: &Config, who: &Who, id: &str) -> Value {
    match schedule_card(cfg, id, "delete") {
        Some((what, card)) => propose(cfg, &Who { user: None, ..*who }, "delete_schedule", json!({ "schedule_id": id }), format!("Delete: {what}"), card),
        None => error(format!("no schedule {id}")),
    }
}

pub fn propose_pause_schedule(cfg: &Config, who: &Who, id: &str, paused: bool) -> Value {
    match schedule_card(cfg, id, if paused { "pause" } else { "resume" }) {
        Some((what, card)) => propose(
            cfg,
            &Who { user: None, ..*who },
            "pause_schedule",
            json!({ "schedule_id": id, "paused": paused }),
            format!("{}{what}", if paused { "Pause: " } else { "Resume: " }),
            card,
        ),
        None => error(format!("no schedule {id}")),
    }
}

/// Who started the restream that's on now (from the action log).
fn streaming_by(cfg: &Config) -> Option<String> {
    history(cfg, 200)
        .into_iter()
        .find(|h| (h["action"] == "start_restream" || h["action"] == "stop_restream") && h["ok"] == true)
        .filter(|h| h["action"] == "start_restream")
        .and_then(|h| h["user"].as_str().map(str::to_string))
}

/// A recording running now: the provider allows one connection, so a restream would cut it.
fn recording_now(cfg: &Config) -> Option<String> {
    crate::status::active_recordings(&cfg.data_dir).into_iter().next().map(|r| r.what)
}

pub fn propose_start_restream(cfg: &Config, who: &Who, stream_id: i64, channel_name: &str) -> Value {
    if let Some(what) = recording_now(cfg) {
        return error(format!(
            "a recording is running ({what}); the provider allows one connection, so a restream now would cut it. Tell the user it can go live once the recording is done"
        ));
    }
    let live = restream::active(&cfg.data_dir);
    let by = live.as_ref().and_then(|_| streaming_by(cfg));
    let by_text = by.as_ref().map(|b| format!(" (started by {b})")).unwrap_or_default();
    let summary = match &live {
        Some(a) => format!("Replace {}{by_text} with {channel_name} on live.dresdell.com", a.channel_name),
        None => format!("Restream {channel_name} on live.dresdell.com"),
    };
    let card = card::restream_start(channel_name, live.as_ref().map(|a| a.channel_name.as_str()));
    let mut out = propose(cfg, who, "start_restream", json!({ "stream_id": stream_id, "channel_name": channel_name }), summary, card);
    if let Some(a) = live {
        out["warning"] = format!(
            "Something is already live: {}{}. Tell the user it will be replaced if they confirm.",
            a.channel_name,
            by.map(|b| format!(", started by {b}")).unwrap_or_default()
        )
        .into();
    }
    out
}

pub fn propose_stop_restream(cfg: &Config, who: &Who) -> Value {
    match restream::active(&cfg.data_dir) {
        Some(a) => {
            let card = card::restream_stop(&a.channel_name);
            propose(cfg, &Who { user: None, ..*who }, "stop_restream", json!({}), format!("Stop restreaming {}", a.channel_name), card)
        }
        None => error("nothing is being restreamed right now"),
    }
}

/// Rebuild a library episode from the recording pieces still on disk.
pub fn propose_retry_run(cfg: &Config, who: &Who, run_id: &str) -> Value {
    let Some(d) = crate::jp::eyes::load_run(cfg, run_id) else {
        return error(format!("no recording report named {run_id}"));
    };
    let Some(show) = d["show_name"].as_str() else {
        return error("that recording was not meant for the Jellyfin library, so there is nothing to rebuild");
    };
    if let Some(placed) = d["result"]["episode_path"].as_str()
        && Path::new(placed).exists()
    {
        return error(format!("an episode was already placed ({}); nothing to rebuild", file_name(placed)));
    }
    let segments = crate::pipeline::find_segments(Path::new(d["output"].as_str().unwrap_or("")));
    if segments.is_empty() {
        return error("the recording pieces are no longer on disk, so it cannot be rebuilt");
    }
    let air: String = d["started_at"].as_str().unwrap_or("").chars().take(10).collect();
    if chrono::NaiveDate::parse_from_str(&air, "%Y-%m-%d").is_err() {
        return error("the report has no valid start date");
    }
    if let Some(existing) = episode_on(cfg, show, &air) {
        return error(format!("the library already has an episode for {air} ({existing}); say so and ask before adding a second one"));
    }
    let total: f64 = segments.iter().filter_map(|s| crate::pipeline::media_seconds(s)).sum();
    let footage = runs::format_seconds(Some(total));
    let summary = format!("Rebuild “{show}” of {air} from {} saved piece(s) ({footage} of footage) and add it to Jellyfin", segments.len());
    let card = card::retry(show, &air, segments.len(), &footage);
    propose(cfg, who, "retry_run", json!({ "run_id": run_id }), summary, card)
}

/// An episode of `show` already in the library for this air date (`… - 2026-10-21.mp4`).
fn episode_on(cfg: &Config, show: &str, air: &str) -> Option<String> {
    let show_dir = cfg.tv_library_root()?.join(show);
    let suffix = format!(" - {air}.mp4");
    std::fs::read_dir(show_dir)
        .ok()?
        .filter_map(Result::ok)
        .filter(|season| season.file_name().to_string_lossy().starts_with("Season "))
        .filter_map(|season| std::fs::read_dir(season.path()).ok())
        .flatten()
        .filter_map(Result::ok)
        .map(|f| f.file_name().to_string_lossy().to_string())
        .find(|name| name.ends_with(&suffix))
}

pub fn propose_rescan_jellyfin(cfg: &Config, who: &Who) -> Value {
    propose(cfg, who, "rescan_jellyfin", json!({}), "Ask Jellyfin to rescan its library now".into(), card::rescan())
}

// ── confirm / cancel (called by the runner, never by the model) ──────────

fn read_pending(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// This chat's proposals still waiting for a tap, oldest first. Expired ones are dropped.
pub fn pending_for(cfg: &Config, session: &str) -> Vec<Value> {
    let Ok(entries) = std::fs::read_dir(pending_dir(cfg)) else { return vec![] };
    let mut out: Vec<Value> = vec![];
    for path in entries.filter_map(Result::ok).map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "json")) {
        let Some(item) = read_pending(&path) else { continue };
        if now() - item["created"].as_f64().unwrap_or(0.0) > PENDING_TTL_SECONDS {
            let _ = std::fs::remove_file(&path);
            continue;
        }
        if item["session"] == session {
            out.push(item);
        }
    }
    out.sort_by(|a, b| a["created"].as_f64().partial_cmp(&b["created"].as_f64()).unwrap_or(std::cmp::Ordering::Equal));
    out
}

fn log(cfg: &Config, mut entry: Value) {
    use std::io::Write;
    let at = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string();
    let mut line = json!({ "at": at });
    if let (Some(line), Some(entry)) = (line.as_object_mut(), entry.as_object_mut()) {
        line.append(entry);
    }
    let _ = std::fs::create_dir_all(agent_dir(cfg)).and_then(|_| {
        let mut f = std::fs::OpenOptions::new().create(true).append(true).open(history_file(cfg))?;
        writeln!(f, "{line}")
    });
}

fn pending_path(cfg: &Config, id: &str) -> Option<PathBuf> {
    (!id.is_empty() && id.chars().all(|c| c.is_ascii_alphanumeric())).then(|| pending_dir(cfg).join(format!("{id}.json")))
}

pub fn cancel(cfg: &Config, id: &str, user: Option<&str>) -> bool {
    let Some(path) = pending_path(cfg, id) else { return false };
    let Some(item) = read_pending(&path) else { return false };
    let _ = std::fs::remove_file(&path);
    log(cfg, json!({ "action": "cancelled", "summary": item["summary"], "user": user }));
    true
}

pub fn execute(cfg: &Config, id: &str, user: Option<&str>, owner: bool) -> Value {
    let Some(path) = pending_path(cfg, id) else {
        return json!({ "ok": false, "message": "Je ne trouve pas cette action." });
    };
    let Some(item) = read_pending(&path) else {
        return json!({ "ok": false, "message": "Cette action a expiré ou a déjà été faite." });
    };
    let kind = item["kind"].as_str().unwrap_or("").to_string();
    if !owner && !FAMILY_ACTIONS.contains(&kind.as_str()) {
        return json!({ "ok": false, "message": "Seul le propriétaire peut faire celle-là." });
    }
    let _ = std::fs::remove_file(&path);
    let a = &item["args"];
    let result = match run_action(cfg, &kind, a, user) {
        Ok(message) => json!({ "ok": true, "message": message }),
        Err(e) => json!({ "ok": false, "message": format!("{e:#}") }),
    };
    log(cfg, json!({ "action": kind, "summary": item["summary"], "ok": result["ok"], "message": result["message"], "user": user }));
    result
}

fn run_action(cfg: &Config, kind: &str, a: &Value, user: Option<&str>) -> anyhow::Result<String> {
    let text = |key: &str| a[key].as_str().unwrap_or("").to_string();
    match kind {
        "create_schedule" => {
            let days: Vec<String> = a["days"].as_array().map(|d| d.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
            let (channel, time, show) = (text("channel_name"), text("time"), a["show_name"].as_str().map(str::to_string));
            let s = schedules::create(
                cfg,
                schedules::New {
                    stream_id: a["stream_id"].as_i64().unwrap_or(0),
                    channel_name: &channel,
                    time: &time,
                    duration_min: a["duration_min"].as_i64().unwrap_or(0),
                    days,
                    date: a["date"].as_str().map(str::to_string),
                    show_name: show.as_deref(),
                    notify: a["notify"].as_bool().unwrap_or(true),
                    created_by: user.unwrap_or("agent"),
                },
            )?;
            Ok(match s.show_name.as_deref() {
                Some(show) => format!("C'est prévu. Ça va aller dans Jellyfin, « {show} »."),
                None => "C'est prévu.".to_string(),
            })
        }
        "delete_schedule" => {
            schedules::delete(cfg, &text("schedule_id"))?;
            Ok("C'est supprimé.".into())
        }
        "pause_schedule" => {
            let paused = a["paused"].as_bool().unwrap_or(true);
            schedules::set_enabled(cfg, &text("schedule_id"), !paused)?;
            Ok(if paused { "C'est en pause." } else { "C'est reparti." }.into())
        }
        "start_restream" => {
            let stream_id = a["stream_id"].as_i64().unwrap_or(0);
            let channel = db::Db::open(&cfg.data_dir)?
                .channel(stream_id)?
                .unwrap_or(db::Channel { stream_id, name: text("channel_name"), category: None });
            if let Some(what) = recording_now(cfg) {
                anyhow::bail!("Un enregistrement est en cours ({what}). La diffusion le couperait : réessaie quand il sera fini.");
            }
            restream::start(cfg, &channel, true)?;
            Ok("C'est en direct sur live.dresdell.com dans quelques secondes.".into())
        }
        "stop_restream" => {
            restream::stop(cfg)?;
            Ok("C'est arrêté.".into())
        }
        "retry_run" => {
            start_retry(cfg, &text("run_id"))?;
            Ok("C'est parti. Ça prend une minute ou deux, et Discord va te le dire.".into())
        }
        "rescan_jellyfin" => match notify::jellyfin_scan(cfg) {
            Ok(()) => Ok("Jellyfin rescanne la bibliothèque.".into()),
            Err(why) => anyhow::bail!("Jellyfin n'a pas pu rescanner ({why})."),
        },
        other => anyhow::bail!("unknown action {other}"),
    }
}

/// The rebuild runs as its own systemd unit (it can take a minute or two).
fn start_retry(cfg: &Config, run_id: &str) -> anyhow::Result<()> {
    let exe = crate::systemd::kdtv_exe()?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.extend(["retry-run".to_string(), run_id.to_string()]);
    crate::systemd::run(crate::systemd::Run {
        unit: &format!("kdcli-retry-{}", chrono::Local::now().format("%Y%m%d-%H%M%S")),
        description: &format!("KDTV: rebuild {run_id} from its saved pieces"),
        working_dir: &cfg.cli_dir,
        max_seconds: Some(3600),
        command,
    })
}

/// The latest actions, newest first.
pub fn history(cfg: &Config, limit: usize) -> Vec<Value> {
    let text = std::fs::read_to_string(history_file(cfg)).unwrap_or_default();
    let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    lines[lines.len().saturating_sub(limit)..].iter().rev().filter_map(|l| serde_json::from_str(l).ok()).collect()
}

fn file_name(path: &str) -> String {
    Path::new(path).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::Title;

    #[test]
    fn titles_lose_their_language_4k_and_year() {
        let t = Title::parse("4K-FR - Dune  (2021)");
        assert_eq!((t.title.as_str(), t.language.as_str(), t.uhd, t.year.as_deref()), ("Dune", "FR", true, Some("2021")));
        let t = Title::parse("FR - Breaking Bad");
        assert_eq!((t.title.as_str(), t.language.as_str(), t.uhd, t.year), ("Breaking Bad", "FR", false, None));
        let t = Title::parse("IT - Breaking Bad (2008) (US)");
        assert_eq!((t.title.as_str(), t.language.as_str()), ("Breaking Bad (2008) (US)", "IT"));
        assert_eq!(Title::parse("Some Show").language, "");
    }
}
