//! What a Confirm card on the OMT page says, in plain French: a label, a title, a
//! few short lines (when, which channel, where it goes) and the two buttons. The
//! family checks it at a glance before anything happens, so no raw recorder
//! strings, ids or arrows here; the one-line `summary` keeps those for the logs.

use chrono::{Datelike, Duration, Local, NaiveDate, NaiveTime};
use regex::Regex;
use serde_json::{json, Value};
use std::sync::OnceLock;

fn card(label: &str, title: &str, lines: Vec<(&str, String)>, yes: &str) -> Value {
    json!({
        "label": label,
        "title": title,
        "lines": lines.into_iter().map(|(icon, text)| json!({ "icon": icon, "text": text })).collect::<Vec<_>>(),
        "yes": yes,
        "no": "Non, laisse faire",
    })
}

/// "US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET" →
/// ("US (ESPN+ 266)", Some("Northeastern vs Massachusetts")).
pub fn split_channel(name: &str) -> (String, Option<String>) {
    static DATE: OnceLock<Regex> = OnceLock::new();
    static RANK: OnceLock<Regex> = OnceLock::new();
    let date = DATE.get_or_init(|| Regex::new(r"(?i)\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b.*$").unwrap());
    let rank = RANK.get_or_init(|| Regex::new(r"#\d+\s*").unwrap());
    match name.split_once(" | ") {
        Some((channel, event)) => {
            let event = rank.replace_all(&date.replace(event.trim(), ""), "").replace(" vs. ", " vs ");
            let event = event.trim().trim_end_matches(['(', ')', ' ']).to_string();
            (channel.trim().to_string(), (!event.is_empty()).then_some(event))
        }
        None => (name.trim().to_string(), None),
    }
}

const DAYS_FR: [&str; 7] = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
const MONTHS_FR: [&str; 12] = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];

/// 18:29 → "18 h 29"; 9:00 → "9 h".
pub fn time_fr(t: NaiveTime) -> String {
    match t.format("%M").to_string().as_str() {
        "00" => format!("{} h", t.format("%-H")),
        m => format!("{} h {m}", t.format("%-H")),
    }
}

/// "samedi 17 oct.", or "Ce soir" / "Aujourd'hui" / "Demain" when close.
pub fn day_fr(d: NaiveDate, start: NaiveTime) -> String {
    let today = Local::now().date_naive();
    if d == today {
        return if start >= NaiveTime::from_hms_opt(17, 0, 0).unwrap_or_default() { "Ce soir".into() } else { "Aujourd'hui".into() };
    }
    if d == today + Duration::days(1) {
        return "Demain".into();
    }
    format!("{} {} {}", DAYS_FR[d.weekday().num_days_from_monday() as usize], d.day(), MONTHS_FR[d.month0() as usize])
}

/// ["Mon","Tue","Wed","Thu"] → "Du lundi au jeudi"; ["Sun"] → "Chaque dimanche".
pub fn days_fr(days: &[String]) -> String {
    let idx: Vec<usize> = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        .iter()
        .enumerate()
        .filter(|(_, d)| days.iter().any(|x| x == *d))
        .map(|(i, _)| i)
        .collect();
    match idx.as_slice() {
        [] => String::new(),
        [0, 1, 2, 3, 4, 5, 6] => "Tous les jours".into(),
        [one] => format!("Chaque {}", DAYS_FR[*one]),
        [first, .., last] if idx.windows(2).all(|w| w[1] == w[0] + 1) => format!("Du {} au {}", DAYS_FR[*first], DAYS_FR[*last]),
        many => {
            let names: Vec<&str> = many.iter().map(|i| DAYS_FR[*i]).collect();
            format!("Chaque {} et {}", names[..names.len() - 1].join(", "), names[names.len() - 1])
        }
    }
}

/// "Ce soir · 17 h 57 à 20 h 47" or "Du lundi au jeudi · 18 h 29 à 19 h 04".
pub fn when_line(days: &[String], date: Option<&str>, time: &str, minutes: i64) -> String {
    let Ok(start) = NaiveTime::parse_from_str(time, "%H:%M") else { return time.to_string() };
    let end = start + Duration::minutes(minutes);
    let span = format!("{} à {}", time_fr(start), time_fr(end));
    match (days.is_empty(), date.and_then(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())) {
        (false, _) => format!("{} · {span}", days_fr(days)),
        (true, Some(d)) => format!("{} · {span}", day_fr(d, start)),
        (true, None) => span,
    }
}

fn where_line(show: Option<&str>, notify: bool) -> String {
    match show {
        Some(s) => format!("Dans Jellyfin, « {s} »"),
        None if notify => "Dans les enregistrements, avec un message Discord".into(),
        None => "Dans les enregistrements".into(),
    }
}

/// The title of a recording: the game or show, else the channel.
fn recording_title(channel: &str, show: Option<&str>) -> (String, String) {
    let (chan, event) = split_channel(channel);
    let title = event.or_else(|| show.map(str::to_string)).unwrap_or_else(|| chan.clone());
    (title, chan)
}

pub struct Rec<'a> {
    pub channel: &'a str,
    pub time: &'a str,
    pub minutes: i64,
    pub days: &'a [String],
    pub date: Option<&'a str>,
    pub show: Option<&'a str>,
    pub notify: bool,
}

/// When, which channel (left out when the title already is the channel), where it goes.
fn recording_lines(r: &Rec, with_where: bool) -> (String, Vec<(&'static str, String)>) {
    let (title, chan) = recording_title(r.channel, r.show);
    let mut lines = vec![("when", when_line(r.days, r.date, r.time, r.minutes))];
    if chan != title {
        lines.push(("channel", chan));
    }
    if with_where {
        lines.push(("where", where_line(r.show, r.notify)));
    }
    (title, lines)
}

pub fn recording(r: &Rec) -> Value {
    let (title, lines) = recording_lines(r, true);
    card("Enregistrement à confirmer", &title, lines, "Oui, enregistre")
}

/// Deleting, pausing or resuming an existing schedule.
pub fn schedule_change(r: &Rec, what: &str) -> Value {
    let (title, lines) = recording_lines(r, false);
    let (label, yes) = match what {
        "delete" => ("Suppression à confirmer", "Oui, supprime"),
        "pause" => ("Pause à confirmer", "Oui, mets en pause"),
        _ => ("Reprise à confirmer", "Oui, reprends"),
    };
    card(label, &title, lines, yes)
}

pub fn restream_start(channel: &str, replacing: Option<&str>) -> Value {
    let (chan, event) = split_channel(channel);
    let mut lines = vec![("where", "En direct sur live.dresdell.com".to_string())];
    if event.is_some() {
        lines.insert(0, ("channel", chan));
    }
    if let Some(old) = replacing {
        lines.push(("warning", format!("Remplace ce qui joue : {}", split_channel(old).1.unwrap_or_else(|| split_channel(old).0))));
    }
    card("Diffusion à confirmer", &event.unwrap_or_else(|| channel.to_string()), lines, "Oui, mets-le en direct")
}

pub fn restream_stop(channel: &str) -> Value {
    let (chan, event) = split_channel(channel);
    let lines = match event {
        Some(_) => vec![("channel", chan.clone())],
        None => vec![("where", "Sur live.dresdell.com".to_string())],
    };
    card("Arrêt à confirmer", &event.unwrap_or(chan), lines, "Oui, arrête")
}

pub fn retry(show: &str, air: &str, pieces: usize, footage: &str) -> Value {
    let air = NaiveDate::parse_from_str(air, "%Y-%m-%d")
        .map(|d| format!("{} {} {}", DAYS_FR[d.weekday().num_days_from_monday() as usize], d.day(), MONTHS_FR[d.month0() as usize]))
        .unwrap_or_else(|_| air.to_string());
    card(
        "Reconstruction à confirmer",
        show,
        vec![("when", format!("L'épisode du {air}")), ("channel", format!("{pieces} morceau(x) gardé(s), {footage} de vidéo")), ("where", format!("Dans Jellyfin, « {show} »"))],
        "Oui, reconstruis",
    )
}

pub fn rescan() -> Value {
    card("Jellyfin", "Rescanner la bibliothèque", vec![("where", "Les nouveaux épisodes vont apparaître".into())], "Oui, rescanne")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn event_channels_read_like_a_game() {
        let (chan, event) = split_channel("US (ESPN+ 266) | Northeastern vs. #19 Massachusetts Oct 17 5:30PM ET");
        assert_eq!(chan, "US (ESPN+ 266)");
        assert_eq!(event.as_deref(), Some("Northeastern vs Massachusetts"));
        assert_eq!(split_channel("CA FR: NOOVO V-TELE HD"), ("CA FR: NOOVO V-TELE HD".to_string(), None));
    }

    #[test]
    fn french_days_and_times() {
        let t = |s| NaiveTime::parse_from_str(s, "%H:%M").unwrap();
        assert_eq!(time_fr(t("18:29")), "18 h 29");
        assert_eq!(time_fr(t("09:00")), "9 h");
        let d = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(days_fr(&d(&["Mon", "Tue", "Wed", "Thu"])), "Du lundi au jeudi");
        assert_eq!(days_fr(&d(&["Sun"])), "Chaque dimanche");
        assert_eq!(days_fr(&d(&["Mon", "Wed", "Fri"])), "Chaque lundi, mercredi et vendredi");
        assert_eq!(when_line(&d(&["Sun"]), None, "18:29", 152), "Chaque dimanche · 18 h 29 à 21 h 01");
        assert_eq!(when_line(&[], Some("2026-10-17"), "17:57", 170).split(" · ").nth(1), Some("17 h 57 à 20 h 47"));
    }
}
