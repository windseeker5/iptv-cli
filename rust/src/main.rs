//! kdtv: KDTV CLI, the brain of KD TV. One binary for the TUI and scriptable
//! commands (`--json` for JP and scripts); later, the background API.
//!
//! kdtv reads the data the Python side writes and uses the proven Python recorder
//! for the recording itself. Long-running work (recordings, the restream, the
//! download queue) runs as systemd user units, so it outlives kdtv.

mod config;
mod db;
mod downloads;
mod favorites;
mod library;
mod log;
mod play;
mod provider;
mod record;
mod refresh;
mod restream;
mod runs;
mod schedules;
mod status;
mod systemd;
mod tui;
mod youtube;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use serde::Serialize;
use serde_json::json;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "kdtv", version, about = "KDTV CLI: live TV, movies, series, YouTube, recordings and restreams")]
struct Cli {
    /// Print JSON instead of text (for JP and scripts)
    #[arg(long, global = true)]
    json: bool,
    /// Use this kdtv.toml instead of looking for one
    #[arg(long, global = true, value_name = "FILE")]
    config: Option<PathBuf>,
    #[command(subcommand)]
    command: Option<Cmd>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Open the full-screen interface (the default)
    Tui,
    /// Live channels
    Channels {
        #[command(subcommand)]
        command: SearchCmd,
    },
    /// What's on a channel now and next
    Guide {
        stream_id: i64,
        /// How many shows to list
        #[arg(long, default_value_t = 4)]
        count: usize,
    },
    /// Movies
    Movies {
        #[command(subcommand)]
        command: SearchCmd,
    },
    /// Series and their episodes
    Series {
        #[command(subcommand)]
        command: SeriesCmd,
    },
    /// Watch a live channel in mpv
    Play {
        stream_id: i64,
        /// Play even if a recording is using the provider connection
        #[arg(long)]
        force: bool,
    },
    /// Record a live channel now
    Record {
        stream_id: i64,
        /// How long; default: until the current show ends
        #[arg(long)]
        minutes: Option<i64>,
        /// Put the recording into Jellyfin as this show
        #[arg(long)]
        show: Option<String>,
        /// Record even if another recording or the restream uses the connection
        #[arg(long)]
        force: bool,
    },
    /// Stop a running recording (unit name from `kd status`)
    Stop { unit: String },
    /// List recording schedules
    Schedules,
    /// Add, pause, resume or delete a recording schedule
    Schedule {
        #[command(subcommand)]
        command: ScheduleCmd,
    },
    /// Latest recordings from the run diary
    Recordings {
        #[arg(long, default_value_t = 10)]
        limit: usize,
    },
    /// Restream a channel for the family
    Restream {
        #[command(subcommand)]
        command: RestreamCmd,
    },
    /// Queue a movie, an episode or a whole series for download
    Download {
        #[command(subcommand)]
        command: DownloadCmd,
    },
    /// The download queue
    Downloads {
        /// Cancel this download
        #[arg(long, value_name = "ID")]
        cancel: Option<String>,
    },
    /// Search YouTube
    Youtube {
        #[arg(required = true)]
        query: Vec<String>,
        #[arg(long, default_value_t = 15)]
        limit: usize,
    },
    /// Download the provider's channel list now (add --guide for the full TV guide)
    Refresh {
        #[arg(long)]
        guide: bool,
    },
    /// Recordings and downloads on disk
    Library {
        #[arg(long, default_value_t = 30)]
        limit: usize,
    },
    /// Services, active recordings and restream, data freshness
    Status,
    #[command(hide = true)]
    RestreamWorker { stream_id: i64 },
    #[command(hide = true)]
    DownloadWorker,
}

#[derive(Subcommand)]
enum SearchCmd {
    /// Find by name, e.g. `kd channels search tva sports`
    Search {
        #[arg(required = true)]
        query: Vec<String>,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
}

#[derive(Subcommand)]
enum SeriesCmd {
    /// Find series by name
    Search {
        #[arg(required = true)]
        query: Vec<String>,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
    /// List a series' episodes (downloads the list from the provider the first time)
    Episodes { series_id: i64 },
}

#[derive(Subcommand)]
enum ScheduleCmd {
    /// e.g. `kd schedule add 412858 --time 18:29 --minutes 35 --days Mon,Tue --show "Occupation Double"`
    Add {
        stream_id: i64,
        /// Local time, like 18:29
        #[arg(long)]
        time: String,
        #[arg(long)]
        minutes: i64,
        /// Repeat on these days (Mon,Tue,...)
        #[arg(long, value_delimiter = ',')]
        days: Vec<String>,
        /// Once, on this date (YYYY-MM-DD); default: the next time it comes
        #[arg(long)]
        date: Option<String>,
        /// Put the recording into Jellyfin as this show
        #[arg(long)]
        show: Option<String>,
    },
    Pause { id: String },
    Resume { id: String },
    Delete { id: String },
}

#[derive(Subcommand)]
enum RestreamCmd {
    Start {
        stream_id: i64,
        /// Switch channels if a restream is already running
        #[arg(long)]
        replace: bool,
        /// Start even if a recording uses the connection
        #[arg(long)]
        force: bool,
    },
    Stop,
}

#[derive(Subcommand)]
enum DownloadCmd {
    Movie { stream_id: i64 },
    Episode { episode_id: String },
    /// Every episode, or one season with --season
    Series {
        series_id: i64,
        #[arg(long)]
        season: Option<i64>,
    },
    /// A YouTube video (add --audio for an mp3)
    Youtube {
        url: String,
        #[arg(long)]
        audio: bool,
    },
}

fn main() {
    // Exit quietly when the reader goes away (`kd movies search x | head`).
    // SAFETY: restoring the default SIGPIPE action before any thread exists.
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
    }
    let cli = Cli::parse();
    if let Err(err) = run(cli) {
        eprintln!("kd: {err:#}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<()> {
    let cfg = config::Config::load(cli.config.as_deref())?;
    let json = cli.json;
    let open = || db::Db::open(&cfg.data_dir);
    let now = || chrono::Utc::now().timestamp();
    match cli.command.unwrap_or(Cmd::Tui) {
        Cmd::Tui => tui::run(&cfg),

        Cmd::Channels { command: SearchCmd::Search { query, limit } } => {
            let channels = open()?.search_channels(&query.join(" "), limit)?;
            let (list_age, guide_age) = status::list_age_minutes(&cfg.data_dir);
            print(json, &json!({ "channels": channels, "list_age_minutes": list_age, "guide_age_minutes": guide_age }), || {
                for c in &channels {
                    println!("{:>9}  {}  {}", c.stream_id, c.name, dim(c.category.as_deref().unwrap_or("")));
                }
                if channels.is_empty() {
                    println!("No channel matches.");
                }
            })
        }

        Cmd::Guide { stream_id, count } => {
            let db = open()?;
            let programs = db.guide(stream_id, now(), count)?;
            let mut out = json!({ "programs": programs });
            if programs.is_empty() {
                out["note"] = "No guide information for this channel.".into();
            }
            print(json, &out, || {
                if let Some(c) = db.channel(stream_id).ok().flatten() {
                    println!("{}\n", c.name);
                }
                for p in &programs {
                    println!("{} – {}  {}", p.start, p.end, p.title.as_deref().unwrap_or("(no title)"));
                }
                if programs.is_empty() {
                    println!("No guide information for this channel.");
                }
            })
        }

        Cmd::Movies { command: SearchCmd::Search { query, limit } } => {
            let movies = open()?.search_movies(&query.join(" "), limit)?;
            print(json, &movies, || {
                for m in &movies {
                    let extra = [m.year.clone(), m.rating.map(|r| format!("★ {r:.1}"))].into_iter().flatten().collect::<Vec<_>>().join("  ");
                    println!("{:>9}  {}  {}", m.stream_id, m.name, dim(&extra));
                }
            })
        }

        Cmd::Series { command: SeriesCmd::Search { query, limit } } => {
            let list = open()?.search_series(&query.join(" "), limit)?;
            print(json, &list, || {
                for s in &list {
                    println!("{:>9}  {}  {}", s.series_id, s.name, dim(s.category.as_deref().unwrap_or("")));
                }
            })
        }

        Cmd::Series { command: SeriesCmd::Episodes { series_id } } => {
            let episodes = episodes(&cfg, series_id)?;
            print(json, &episodes, || {
                for e in &episodes {
                    println!("{:>10}  S{:02}E{:02}  {}", e.episode_id, e.season, e.episode_num, e.title);
                }
            })
        }

        Cmd::Play { stream_id, force } => {
            let db = open()?;
            let channel = db.channel(stream_id)?.with_context(|| format!("no live channel with id {stream_id}"))?;
            if !force && status::collect(&cfg, Some(&db)).connection_busy() {
                bail!("a recording or restream is using the provider connection; playing now could cut it (use --force to play anyway)");
            }
            play::play(&cfg, &channel.name, play::Target::Live(stream_id))?;
            print(json, &json!({ "playing": channel }), || println!("Playing {}", channel.name))
        }

        Cmd::Record { stream_id, minutes, show, force } => {
            let db = open()?;
            let channel = db.channel(stream_id)?.with_context(|| format!("no live channel with id {stream_id}"))?;
            if !force && status::collect(&cfg, Some(&db)).connection_busy() {
                bail!("a recording or restream is already using the provider connection (use --force to record anyway)");
            }
            let minutes = match minutes {
                Some(m) => m,
                None => minutes_until_show_ends(&db, stream_id, now())
                    .context("no guide information to know when the show ends; give --minutes")?,
            };
            let started = record::record_now(
                &cfg,
                &record::Request { stream_id, channel_name: &channel.name, minutes, show_name: show.as_deref() },
            )?;
            print(json, &started, || {
                println!("Recording {} for {} min → {}", channel.name, started.minutes, started.output);
                if let Some(w) = &started.warning {
                    println!("{}", dim(w));
                }
            })
        }

        Cmd::Stop { unit } => {
            record::stop(&cfg, &unit)?;
            print(json, &json!({ "stopped": unit }), || println!("Stopped {unit}"))
        }

        Cmd::Schedules => {
            let list = schedules::list(&cfg.data_dir);
            print(json, &list, || {
                for s in &list {
                    let state = if s.enabled { "on " } else { "off" };
                    println!("{state}  {}", s.what);
                    println!("     {}", dim(&format!("next: {}   id: {}", s.next_run.as_deref().unwrap_or("-"), s.id)));
                }
                if list.is_empty() {
                    println!("No schedules.");
                }
            })
        }

        Cmd::Schedule { command } => {
            let s = match command {
                ScheduleCmd::Add { stream_id, time, minutes, days, date, show } => {
                    let channel = open()?.channel(stream_id)?.with_context(|| format!("no live channel with id {stream_id}"))?;
                    let date = if days.is_empty() { Some(date.unwrap_or_else(|| next_date_for(&time))) } else { None };
                    schedules::create(
                        &cfg,
                        schedules::New {
                            stream_id,
                            channel_name: &channel.name,
                            time: &time,
                            duration_min: minutes,
                            days,
                            date,
                            show_name: show.as_deref(),
                            created_by: "kd",
                        },
                    )?
                }
                ScheduleCmd::Pause { id } => schedules::set_enabled(&cfg, &id, false)?,
                ScheduleCmd::Resume { id } => schedules::set_enabled(&cfg, &id, true)?,
                ScheduleCmd::Delete { id } => {
                    let existed = schedules::delete(&cfg, &id)?;
                    return print(json, &json!({ "deleted": id, "existed": existed }), || {
                        println!("{}", if existed { format!("Deleted {id}") } else { format!("No schedule {id}") })
                    });
                }
            };
            print(json, &s, || println!("{}  {}\n     {}", if s.enabled { "on " } else { "off" }, s.what, dim(&format!("id: {}", s.id))))
        }

        Cmd::Recordings { limit } => {
            let list = runs::recent(&cfg.data_dir, limit);
            print(json, &list, || {
                for r in &list {
                    println!(
                        "{:<8} {}  {}  {} / {}",
                        r.status,
                        r.start_time.as_deref().unwrap_or("?"),
                        r.show_name.as_deref().or(r.channel_name.as_deref()).unwrap_or("?"),
                        r.recorded,
                        r.planned
                    );
                    for p in r.problems.iter().take(2) {
                        println!("         {}", dim(p));
                    }
                    if r.problems.len() > 2 {
                        println!("         {}", dim(&format!("… {} more (kd recordings --json)", r.problems.len() - 2)));
                    }
                }
            })
        }

        Cmd::Restream { command: RestreamCmd::Start { stream_id, replace, force } } => {
            let db = open()?;
            let channel = db.channel(stream_id)?.with_context(|| format!("no live channel with id {stream_id}"))?;
            if !force && !status::collect(&cfg, Some(&db)).recording.is_empty() {
                bail!("a recording is using the provider connection; restreaming now could cut it (use --force)");
            }
            let started = restream::start(&cfg, &channel, replace)?;
            print(json, &started, || println!("Restreaming {} → {}", started.channel_name, started.watch_url))
        }

        Cmd::Restream { command: RestreamCmd::Stop } => {
            let stopped = restream::stop(&cfg)?;
            print(json, &json!({ "stopped": stopped }), || match &stopped {
                Some(name) => println!("Stopped the restream of {name}"),
                None => println!("No restream was running."),
            })
        }

        Cmd::Download { command } => {
            let db = open()?;
            let jobs = match command {
                DownloadCmd::Movie { stream_id } => {
                    let movie = db.movie(stream_id)?.with_context(|| format!("no movie with id {stream_id}"))?;
                    vec![downloads::add_movie(&cfg, &movie)?]
                }
                DownloadCmd::Episode { episode_id } => {
                    let ep = db.episode(&episode_id)?.with_context(|| format!("no cached episode {episode_id}; run `kd series episodes` first"))?;
                    let series = db.series(ep.series_id)?.map(|s| s.name).unwrap_or_else(|| "Series".into());
                    downloads::add_episodes(&cfg, &series, &[ep])?
                }
                DownloadCmd::Series { series_id, season } => {
                    let series = db.series(series_id)?.with_context(|| format!("no series with id {series_id}"))?;
                    let all = episodes(&cfg, series_id)?;
                    let chosen: Vec<_> = all.into_iter().filter(|e| season.is_none_or(|s| e.season == s)).collect();
                    downloads::add_episodes(&cfg, &series.name, &chosen)?
                }
                DownloadCmd::Youtube { url, audio } => {
                    let video = youtube::Video { id: String::new(), title: url.clone(), channel: String::new(), duration: 0, url };
                    vec![downloads::add_youtube(&cfg, &video, audio)?]
                }
            };
            print(json, &jobs, || {
                for j in &jobs {
                    println!("Queued {}  {}", j.title, dim(&j.output));
                }
            })
        }

        Cmd::Downloads { cancel } => {
            if let Some(id) = cancel {
                let job = downloads::cancel(&cfg, &id)?;
                return print(json, &job, || println!("Cancelled {}", job.title));
            }
            let jobs = downloads::list(&cfg);
            print(json, &jobs, || {
                for j in &jobs {
                    let state = format!("{:?}", j.state).to_lowercase();
                    let progress = j.progress().map(|p| format!("{p:.0}%")).unwrap_or_else(|| library::human_size(j.bytes));
                    println!("{state:<11} {}  {}  {}", j.title, dim(&progress), dim(&j.id));
                    if let Some(e) = &j.error {
                        println!("            {}", dim(e));
                    }
                }
                if jobs.is_empty() {
                    println!("No downloads.");
                }
            })
        }

        Cmd::Youtube { query, limit } => {
            let videos = youtube::search(&query.join(" "), limit)?;
            print(json, &videos, || {
                for v in &videos {
                    println!("{:>8}  {}  {}", youtube::duration(v.duration), v.title, dim(&format!("{} · {}", v.channel, v.url)));
                }
            })
        }

        Cmd::Refresh { guide } => {
            let message = refresh::run(&cfg, guide)?;
            print(json, &json!({ "ok": true, "message": message }), || println!("{message}"))
        }

        Cmd::Library { limit } => {
            let mut media = library::list(&cfg);
            media.truncate(limit);
            print(json, &media, || {
                for m in &media {
                    println!("{:<10} {:>9}  {}", m.kind, library::human_size(m.size), m.name);
                }
            })
        }

        Cmd::Status => {
            let db = open().ok();
            let s = status::collect(&cfg, db.as_ref());
            print(json, &s, || print_status(&s))
        }

        Cmd::RestreamWorker { stream_id } => restream::worker(&cfg, stream_id),
        Cmd::DownloadWorker => downloads::worker(&cfg),
    }
}

/// Cached episodes, downloading the list from the provider the first time.
pub fn episodes(cfg: &config::Config, series_id: i64) -> Result<Vec<db::Episode>> {
    let cached = db::Db::open(&cfg.data_dir)?.episodes(series_id)?;
    if !cached.is_empty() {
        return Ok(cached);
    }
    provider::fetch_episodes(cfg, series_id)?;
    db::Db::open(&cfg.data_dir)?.episodes(series_id)
}

/// Minutes left in the show on now, plus a little margin for late endings.
pub fn minutes_until_show_ends(db: &db::Db, stream_id: i64, now: i64) -> Option<i64> {
    let show = db.guide(stream_id, now, 1).ok()?.into_iter().next().filter(|p| p.start_ts <= now)?;
    Some(((show.end_ts - now) / 60 + 3).clamp(record::MIN_MINUTES, record::MAX_MINUTES))
}

/// Today if that time is still ahead, else tomorrow (YYYY-MM-DD).
pub fn next_date_for(time: &str) -> String {
    let now = chrono::Local::now().naive_local();
    let today = now.date();
    let later_today = chrono::NaiveTime::parse_from_str(time, "%H:%M").is_ok_and(|t| today.and_time(t) > now);
    let day = if later_today { today } else { today.succ_opt().unwrap_or(today) };
    day.format("%Y-%m-%d").to_string()
}

fn print(json: bool, value: &impl Serialize, text: impl FnOnce()) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(value)?);
    } else {
        text();
    }
    Ok(())
}

fn dim(text: &str) -> String {
    if std::io::IsTerminal::is_terminal(&std::io::stdout()) {
        format!("\x1b[2m{text}\x1b[0m")
    } else {
        text.to_string()
    }
}

fn print_status(s: &status::Status) {
    println!("Profile   {}", s.profile);
    println!("Services");
    for svc in &s.services {
        println!("  {:<8} {:<18} {}", svc.state, svc.name, dim(&svc.detail));
    }
    if s.recording.is_empty() {
        println!("Recording none");
    }
    for r in &s.recording {
        println!("Recording {}  {}", r.what, dim(&r.unit));
    }
    if s.restream.live {
        println!("Restream  {} (since {})", s.restream.channel_name.as_deref().unwrap_or("?"), s.restream.since.as_deref().unwrap_or("?"));
    } else {
        println!("Restream  off");
    }
    let age = |m: Option<i64>| m.map_or("never".to_string(), |m| format!("{m} min ago"));
    println!("Channels  updated {}", age(s.list_age_minutes));
    println!("Guide     updated {}", age(s.guide_age_minutes));
    if let Some(c) = &s.database {
        println!("Database  {} live, {} movies, {} series, {} guide entries", c.live, c.vod, c.series, c.guide_entries);
    }
    if let Some(a) = &s.account {
        println!("Account   {}, expires {}, {} connection(s)", a.status, a.expires.as_deref().unwrap_or("?"), a.max_connections);
    }
}
