//! kd: the KD TV brain. One binary for the TUI, scriptable commands (`--json`
//! for JP and scripts) and, later, the background API.
//!
//! Milestone 1 is read-only: it reads the data the Python side writes and never
//! changes it. kd's own files go to `data/logs/kd/` only.

mod config;
mod db;
mod favorites;
mod log;
mod play;
mod runs;
mod schedules;
mod status;
mod tui;

use anyhow::{bail, Result};
use clap::{Parser, Subcommand};
use serde::Serialize;
use serde_json::json;
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "kd", version, about = "KD TV: live TV, recordings and restreams")]
struct Cli {
    /// Print JSON instead of text (for JP and scripts)
    #[arg(long, global = true)]
    json: bool,
    /// Use this kd.toml instead of looking for one
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
        command: ChannelsCmd,
    },
    /// What's on a channel now and next
    Guide {
        stream_id: i64,
        /// How many shows to list
        #[arg(long, default_value_t = 4)]
        count: usize,
    },
    /// Recording schedules
    Schedules,
    /// Latest recordings from the run diary
    Recordings {
        #[arg(long, default_value_t = 10)]
        limit: usize,
    },
    /// Services, active recordings and restream, data freshness
    Status,
    /// Watch a live channel in mpv
    Play {
        stream_id: i64,
        /// Play even if a recording is using the provider connection
        #[arg(long)]
        force: bool,
    },
}

#[derive(Subcommand)]
enum ChannelsCmd {
    /// Find channels by name, e.g. `kd channels search tva sports`
    Search {
        #[arg(required = true)]
        query: Vec<String>,
        #[arg(long, default_value_t = 20)]
        limit: usize,
    },
}

fn main() {
    let cli = Cli::parse();
    if let Err(err) = run(cli) {
        eprintln!("kd: {err:#}");
        std::process::exit(1);
    }
}

fn run(cli: Cli) -> Result<()> {
    let cfg = config::Config::load(cli.config.as_deref())?;
    let json = cli.json;
    match cli.command.unwrap_or(Cmd::Tui) {
        Cmd::Tui => tui::run(&cfg),

        Cmd::Channels { command: ChannelsCmd::Search { query, limit } } => {
            let channels = db::Db::open(&cfg.data_dir)?.search_channels(&query.join(" "), limit)?;
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
            let db = db::Db::open(&cfg.data_dir)?;
            let programs = db.guide(stream_id, chrono::Utc::now().timestamp(), count)?;
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

        Cmd::Status => {
            let db = db::Db::open(&cfg.data_dir).ok();
            let s = status::collect(&cfg, db.as_ref());
            print(json, &s, || print_status(&s))
        }

        Cmd::Play { stream_id, force } => {
            let db = db::Db::open(&cfg.data_dir)?;
            let Some(channel) = db.channel(stream_id)? else { bail!("no live channel with id {stream_id}") };
            if !force && status::collect(&cfg, Some(&db)).connection_busy() {
                bail!("a recording or restream is using the provider connection; playing now could cut it (use --force to play anyway)");
            }
            play::play(&cfg, &channel)?;
            print(json, &json!({ "playing": channel }), || println!("Playing {}", channel.name))
        }
    }
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
        println!("Recording {}", r.what);
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
