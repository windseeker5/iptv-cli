//! Recording schedules: `data/schedules/<id>.json` plus a systemd user timer
//! `kdcli-rec-<id>.timer` that runs `kdtv run-schedule <id>`.

use crate::config::Config;
use crate::systemd::systemctl;
use anyhow::{bail, Context, Result};
use chrono::{Local, NaiveDate, NaiveDateTime, NaiveTime, Timelike};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::path::{Path, PathBuf};

pub const DAY_NAMES: [&str; 7] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const UNIT_PREFIX: &str = "kdcli-rec-";

/// The file on disk; field for field what `asdict(Schedule)` writes in Python.
#[derive(Serialize, Deserialize, Clone)]
pub struct ScheduleFile {
    pub id: String,
    pub stream_id: i64,
    pub channel_name: String,
    pub time: String,
    pub duration_min: i64,
    #[serde(default)]
    pub days: Vec<String>,
    pub date: Option<String>,
    pub show_name: Option<String>,
    #[serde(default = "yes")]
    pub notify: bool,
    #[serde(default = "yes")]
    pub enabled: bool,
    #[serde(default)]
    pub created_at: String,
    #[serde(default)]
    pub created_by: Option<String>,
}

fn yes() -> bool {
    true
}

impl ScheduleFile {
    fn once(&self) -> bool {
        self.days.is_empty()
    }

    /// A one-time schedule whose date has gone by.
    fn is_past(&self) -> bool {
        self.once() && self.date.as_deref().is_none_or(|d| d < Local::now().format("%Y-%m-%d").to_string().as_str())
    }

    fn fires_on(&self, day: NaiveDate) -> bool {
        if self.once() {
            self.date.as_deref() == Some(day.format("%Y-%m-%d").to_string().as_str())
        } else {
            self.days.contains(&day.format("%a").to_string())
        }
    }

    fn on_calendar(&self) -> String {
        if self.once() {
            format!("{} {}:00", self.date.as_deref().unwrap_or(""), self.time)
        } else {
            format!("{} *-*-* {}:00", self.days.join(","), self.time)
        }
    }

    /// "CA FR: NOOVO V-TELE HD, every Sun at 18:29, 152 min → Jellyfin “Occupation Double”"
    fn describe(&self) -> String {
        let when = if self.once() {
            format!("on {}", self.date.as_deref().unwrap_or("?"))
        } else {
            format!("every {}", self.days.join("/"))
        };
        let into = self.show_name.as_ref().map(|n| format!(" → Jellyfin “{n}”")).unwrap_or_default();
        format!("{}, {} at {}, {} min{}", self.channel_name, when, self.time, self.duration_min, into)
    }
}

/// One schedule, with the same keys JP gets from `list_schedules` today.
#[derive(Serialize)]
pub struct Schedule {
    pub id: String,
    pub what: String,
    pub enabled: bool,
    pub next_run: Option<String>,
    pub stream_id: i64,
    pub channel_name: String,
    pub days: Vec<String>,
    pub date: Option<String>,
    pub time: String,
    pub duration_min: i64,
    pub show_name: Option<String>,
    pub created_by: Option<String>,
}

pub struct New<'a> {
    pub stream_id: i64,
    pub channel_name: &'a str,
    pub time: &'a str,
    pub duration_min: i64,
    pub days: Vec<String>,
    pub date: Option<String>,
    pub show_name: Option<&'a str>,
    /// Ping Discord when a recording that fills no library finishes.
    pub notify: bool,
    pub created_by: &'a str,
}

// ── reading ──────────────────────────────────────────────────────────────

pub fn list(data_dir: &Path) -> Vec<Schedule> {
    files(data_dir).into_iter().map(to_schedule).collect()
}

fn files(data_dir: &Path) -> Vec<ScheduleFile> {
    let Ok(entries) = std::fs::read_dir(data_dir.join("schedules")) else {
        return vec![];
    };
    let mut paths: Vec<_> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    paths.iter().filter_map(|p| read(p)).collect()
}

/// The next time an enabled schedule starts within `minutes` from `from`, and what it is.
/// Recording now must not run into it: the provider allows one connection.
pub fn next_start_within(data_dir: &Path, from: NaiveDateTime, minutes: i64) -> Option<(NaiveDateTime, String)> {
    let until = from + chrono::Duration::minutes(minutes);
    files(data_dir)
        .into_iter()
        .filter(|s| s.enabled && !s.is_past())
        .filter_map(|s| {
            let time = NaiveTime::parse_from_str(&s.time, "%H:%M").ok()?;
            // Today and tomorrow cover any recording up to the six-hour maximum.
            (0..2)
                .map(|d| from.date() + chrono::Duration::days(d))
                .map(|day| day.and_time(time))
                .find(|start| *start > from && *start < until && s.fires_on(start.date()))
                .map(|start| (start, s.describe()))
        })
        .min_by_key(|(start, _)| *start)
}

/// When a schedule records, as minutes of the day on some days (or one date).
struct Window {
    days: Vec<String>,
    date: Option<NaiveDate>,
    start: i64,
    length: i64,
}

impl Window {
    fn of(days: &[String], date: Option<&str>, time: &str, length: i64) -> Window {
        let start = NaiveTime::parse_from_str(time, "%H:%M").map(|t| (t.hour() * 60 + t.minute()) as i64).unwrap_or(0);
        Window { days: days.to_vec(), date: date.and_then(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").ok()), start, length }
    }

    fn overlaps(&self, other: &Window) -> bool {
        let weekday = |d: NaiveDate| d.format("%a").to_string();
        let same_day = match (self.date, other.date) {
            (Some(a), Some(b)) => a == b,
            (Some(a), None) => other.days.contains(&weekday(a)),
            (None, Some(b)) => self.days.contains(&weekday(b)),
            (None, None) => self.days.iter().any(|d| other.days.contains(d)),
        };
        same_day && self.start < other.start + other.length && other.start < self.start + self.length
    }
}

fn to_schedule(s: ScheduleFile) -> Schedule {
    Schedule {
        what: s.describe(),
        next_run: next_run(&s.id),
        id: s.id,
        enabled: s.enabled,
        stream_id: s.stream_id,
        channel_name: s.channel_name,
        days: s.days,
        date: s.date,
        time: s.time,
        duration_min: s.duration_min,
        show_name: s.show_name,
        created_by: s.created_by,
    }
}

fn read(path: &Path) -> Option<ScheduleFile> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

pub fn load(data_dir: &Path, id: &str) -> Option<ScheduleFile> {
    read(&path(data_dir, id).ok()?)
}

/// The description of one schedule, for naming the recording a timer is running.
pub fn describe_id(data_dir: &Path, id: &str) -> Option<String> {
    load(data_dir, id).map(|s| s.describe())
}

pub fn is_one_time(data_dir: &Path, id: &str) -> bool {
    load(data_dir, id).is_some_and(|s| s.once())
}

pub fn next_run(id: &str) -> Option<String> {
    let out = systemctl(&["show", &format!("{UNIT_PREFIX}{id}.timer"), "--property=NextElapseUSecRealtime", "--value"]).ok()?;
    let value = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (!value.is_empty()).then_some(value)
}

fn path(data_dir: &Path, id: &str) -> Result<PathBuf> {
    let ok = (4..=40).contains(&id.len()) && id.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if !ok {
        bail!("bad schedule id: {id}");
    }
    Ok(data_dir.join("schedules").join(format!("{id}.json")))
}

// ── changing ─────────────────────────────────────────────────────────────

pub fn validate(new: &New) -> Vec<String> {
    let mut errors = vec![];
    if new.stream_id <= 0 {
        errors.push("a channel (stream id) is required".to_string());
    }
    if new.channel_name.is_empty() {
        errors.push("a channel name is required".into());
    }
    let time = NaiveTime::parse_from_str(new.time, "%H:%M").ok();
    if time.is_none() {
        errors.push("time must look like 18:29".into());
    }
    if !(crate::record::MIN_MINUTES..=crate::record::MAX_MINUTES).contains(&new.duration_min) {
        errors.push(format!("duration must be between {} and {} minutes", crate::record::MIN_MINUTES, crate::record::MAX_MINUTES));
    }
    if new.days.iter().any(|d| !DAY_NAMES.contains(&d.as_str())) {
        errors.push(format!("days must be among {}", DAY_NAMES.join(", ")));
    }
    if new.days.is_empty() {
        match new.date.as_deref().map(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d")) {
            None => errors.push("give either repeating days or a date".into()),
            Some(Err(_)) => errors.push("date must look like 2026-10-02".into()),
            Some(Ok(date)) => {
                if let Some(time) = time
                    && NaiveDateTime::new(date, time) <= Local::now().naive_local()
                {
                    errors.push("that date and time are already past".into());
                }
            }
        }
    }
    errors
}

/// The description of an enabled schedule the new one would run into, if any.
pub fn clash(data_dir: &Path, new: &New) -> Option<String> {
    let candidate = Window::of(&new.days, new.date.as_deref(), new.time, new.duration_min);
    files(data_dir)
        .into_iter()
        .find(|s| s.enabled && !s.is_past() && Window::of(&s.days, s.date.as_deref(), &s.time, s.duration_min).overlaps(&candidate))
        .map(|s| s.describe())
}

/// How a schedule not yet created will be described, for a Confirm card.
pub fn describe_new(new: &New) -> String {
    let days: Vec<String> = DAY_NAMES.iter().filter(|d| new.days.iter().any(|x| x == *d)).map(|d| d.to_string()).collect();
    ScheduleFile {
        id: String::new(),
        stream_id: new.stream_id,
        channel_name: new.channel_name.to_string(),
        time: new.time.to_string(),
        duration_min: new.duration_min,
        date: if days.is_empty() { new.date.clone() } else { None },
        days,
        show_name: new.show_name.map(str::to_string),
        notify: new.notify,
        enabled: true,
        created_at: String::new(),
        created_by: None,
    }
    .describe()
}

pub fn create(cfg: &Config, new: New) -> Result<Schedule> {
    let errors = validate(&new);
    if !errors.is_empty() {
        bail!("{}", errors.join("; "));
    }
    if let Some(clash) = clash(&cfg.data_dir, &new) {
        bail!("this overlaps “{clash}”; your provider allows one connection, so only one recording can run at a time");
    }
    let days: Vec<String> = DAY_NAMES.iter().filter(|d| new.days.iter().any(|x| x == *d)).map(|d| d.to_string()).collect();
    let time = NaiveTime::parse_from_str(new.time, "%H:%M").map(|t| t.format("%H:%M").to_string())?;
    let s = ScheduleFile {
        id: format!("{}-{}", random_hex(), slug(new.channel_name)),
        stream_id: new.stream_id,
        channel_name: new.channel_name.to_string(),
        time,
        duration_min: new.duration_min,
        date: if days.is_empty() { new.date } else { None },
        days,
        show_name: new.show_name.map(str::trim).filter(|n| !n.is_empty()).map(str::to_string),
        notify: new.notify,
        enabled: true,
        created_at: Local::now().format("%Y-%m-%dT%H:%M:%S").to_string(),
        created_by: Some(new.created_by.to_string()),
    };
    write(&cfg.data_dir, &s)?;
    write_units(cfg, &s)?;
    systemctl(&["daemon-reload"])?;
    let out = systemctl(&["enable", "--now", &format!("{UNIT_PREFIX}{}.timer", s.id)])?;
    if !out.status.success() {
        delete(cfg, &s.id)?;
        bail!("systemd refused the timer: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    crate::log::event(cfg, "schedule.create", json!({ "id": s.id, "what": s.describe(), "by": new.created_by }));
    Ok(to_schedule(s))
}

pub fn set_enabled(cfg: &Config, id: &str, enabled: bool) -> Result<Schedule> {
    let mut s = load(&cfg.data_dir, id).with_context(|| format!("no schedule {id}"))?;
    systemctl(&[if enabled { "enable" } else { "disable" }, "--now", &format!("{UNIT_PREFIX}{id}.timer")])?;
    s.enabled = enabled;
    write(&cfg.data_dir, &s)?;
    crate::log::event(cfg, if enabled { "schedule.resume" } else { "schedule.pause" }, json!({ "id": id }));
    Ok(to_schedule(s))
}

pub fn delete(cfg: &Config, id: &str) -> Result<bool> {
    let file = path(&cfg.data_dir, id)?;
    let existed = file.exists();
    let unit = format!("{UNIT_PREFIX}{id}");
    systemctl(&["disable", "--now", &format!("{unit}.timer")])?;
    let _ = std::fs::remove_file(unit_dir().join(format!("{unit}.timer")));
    // A one-time schedule deletes itself from inside its own run. Removing the
    // service file of a unit that is still running makes systemd forget it has no
    // start timeout: it kills the run and marks it failed. Leave the file for
    // `sweep_leftovers` to remove once the run is over.
    if !crate::systemd::is_running(&format!("{unit}.service")) {
        let _ = std::fs::remove_file(unit_dir().join(format!("{unit}.service")));
    }
    systemctl(&["daemon-reload"])?;
    let _ = std::fs::remove_file(&file);
    crate::log::event(cfg, "schedule.delete", json!({ "id": id, "existed": existed }));
    Ok(existed)
}

/// Remove the service files left by deleted schedules whose run has finished, and
/// forget their failed state. Returns how many were removed.
pub fn sweep_leftovers(cfg: &Config) -> usize {
    let Ok(entries) = std::fs::read_dir(unit_dir()) else {
        return 0;
    };
    let leftovers: Vec<String> = entries
        .filter_map(Result::ok)
        .map(|e| e.file_name().to_string_lossy().to_string())
        .filter(|name| {
            name.strip_prefix(UNIT_PREFIX)
                .and_then(|n| n.strip_suffix(".service"))
                .is_some_and(|id| load(&cfg.data_dir, id).is_none() && !crate::systemd::is_running(name))
        })
        .collect();
    if leftovers.is_empty() {
        return 0;
    }
    for name in &leftovers {
        let _ = std::fs::remove_file(unit_dir().join(name));
        let _ = systemctl(&["reset-failed", name]);
    }
    let _ = systemctl(&["daemon-reload"]);
    crate::log::event(cfg, "schedule.sweep", json!({ "removed": leftovers }));
    leftovers.len()
}

fn write(data_dir: &Path, s: &ScheduleFile) -> Result<()> {
    let file = path(data_dir, &s.id)?;
    std::fs::create_dir_all(file.parent().unwrap_or(data_dir))?;
    std::fs::write(&file, serde_json::to_string_pretty(s)?).with_context(|| format!("writing {}", file.display()))
}

fn unit_dir() -> PathBuf {
    std::env::var("KDCLI_SYSTEMD_USER_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".config/systemd/user"))
}

fn write_units(cfg: &Config, s: &ScheduleFile) -> Result<()> {
    let dir = unit_dir();
    std::fs::create_dir_all(&dir)?;
    let unit = format!("{UNIT_PREFIX}{}", s.id);
    let exe = crate::systemd::kdtv_exe()?;
    let mut command = vec![exe.display().to_string()];
    command.extend(cfg.config_args());
    command.extend(["run-schedule".to_string(), s.id.clone()]);
    std::fs::write(
        dir.join(format!("{unit}.service")),
        format!(
            "[Unit]\nDescription=KDTV recording: {}\n\n[Service]\nType=oneshot\nWorkingDirectory={}\nExecStart={}\n",
            s.describe(),
            cfg.cli_dir.display(),
            command.iter().map(|a| systemd_quote(a)).collect::<Vec<_>>().join(" ")
        ),
    )?;
    std::fs::write(
        dir.join(format!("{unit}.timer")),
        format!(
            "[Unit]\nDescription=When: {}\n\n[Timer]\nOnCalendar={}\nPersistent={}\n\n[Install]\nWantedBy=timers.target\n",
            s.describe(),
            s.on_calendar(),
            if s.once() { "false" } else { "true" }
        ),
    )?;
    Ok(())
}

/// Rewrite every schedule's units for this kdtv program (after installing or
/// moving it), keeping each timer on or off as it was. Returns how many.
pub fn repair(cfg: &Config) -> Result<usize> {
    let all = files(&cfg.data_dir);
    for s in &all {
        write_units(cfg, s)?;
    }
    systemctl(&["daemon-reload"])?;
    for s in all.iter().filter(|s| s.enabled && !s.is_past()) {
        systemctl(&["enable", "--now", &format!("{UNIT_PREFIX}{}.timer", s.id)])?;
    }
    crate::log::event(cfg, "schedule.repair", json!({ "count": all.len() }));
    Ok(all.len())
}

/// An ExecStart argument: quoted when it holds a space, `%` doubled for systemd.
fn systemd_quote(arg: &str) -> String {
    let arg = arg.replace('%', "%%");
    if arg.contains(char::is_whitespace) || arg.contains('"') {
        format!("\"{}\"", arg.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        arg
    }
}

fn random_hex() -> String {
    let mut bytes = [0u8; 4];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        let _ = std::io::Read::read_exact(&mut f, &mut bytes);
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// "CA FR: NOOVO V-TELE HD" → "ca-fr-noovo-v-tele-h" (20 characters at most).
fn slug(name: &str) -> String {
    let mut out = String::new();
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_matches('-').chars().take(20).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_and_descriptions_match_python() {
        assert_eq!(slug("CA FR: NOOVO V-TELE HD"), "ca-fr-noovo-v-tele-h");
        assert_eq!(slug("US (ESPN+ 266) | Northeastern"), "us-espn-266-northeas");
        let s = ScheduleFile {
            id: "x".into(),
            stream_id: 1,
            channel_name: "CA FR: NOOVO V-TELE HD".into(),
            time: "18:29".into(),
            duration_min: 152,
            days: vec!["Sun".into()],
            date: None,
            show_name: Some("Occupation Double".into()),
            notify: true,
            enabled: true,
            created_at: String::new(),
            created_by: None,
        };
        assert_eq!(s.describe(), "CA FR: NOOVO V-TELE HD, every Sun at 18:29, 152 min → Jellyfin “Occupation Double”");
        assert_eq!(s.on_calendar(), "Sun *-*-* 18:29:00");
    }

    #[test]
    fn overlapping_recordings_are_spotted() {
        let w = |days: &[&str], date: Option<&str>, time, len| Window::of(&days.iter().map(|d| d.to_string()).collect::<Vec<_>>(), date, time, len);
        let sunday_od = w(&["Sun"], None, "18:29", 152);
        // 2026-10-18 is a Sunday: tonight's extra one-time copy clashes.
        assert!(w(&[], Some("2026-10-18"), "18:29", 156).overlaps(&sunday_od));
        assert!(w(&[], Some("2026-10-18"), "20:00", 30).overlaps(&sunday_od));
        assert!(!w(&[], Some("2026-10-18"), "21:01", 30).overlaps(&sunday_od));
        assert!(!w(&[], Some("2026-10-19"), "18:29", 30).overlaps(&sunday_od));
        assert!(w(&["Mon", "Sun"], None, "17:00", 90).overlaps(&sunday_od));
        assert!(!w(&["Mon"], None, "18:29", 35).overlaps(&sunday_od));
    }

    #[test]
    fn validation_catches_bad_input() {
        let new = |time, days: Vec<String>, date: Option<String>| New {
            stream_id: 1,
            channel_name: "X",
            time,
            duration_min: 30,
            days,
            date,
            show_name: None,
            notify: true,
            created_by: "test",
        };
        assert!(validate(&new("18:29", vec!["Mon".into()], None)).is_empty());
        assert!(!validate(&new("25:00", vec!["Mon".into()], None)).is_empty());
        assert!(!validate(&new("18:29", vec!["Lun".into()], None)).is_empty());
        assert!(!validate(&new("18:29", vec![], None)).is_empty());
        assert!(!validate(&new("18:29", vec![], Some("2020-01-01".into()))).is_empty());
        assert!(validate(&new("18:29", vec![], Some("2099-01-01".into()))).is_empty());
    }
}
