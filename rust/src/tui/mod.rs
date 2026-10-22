//! The full-screen interface, laid out like design B2:
//!
//! ```text
//! ╭ menu ───────╮╭ list ─────────────────╮╭ details ───────╮
//! │ Search      ││ what's in the section ││ the selection  │
//! │ Live …      ││                       ││ and its        │
//! │ Account …   ││                       ││ actions        │
//! ╰─────────────╯╰───────────────────────╯╰────────────────╯
//!  REC … │ RESTREAMING … │ ↓ download │ message        keys
//! ```
//!
//! ↑↓ move, → or ↵ go into the list, ← or esc go back, Tab switches panel,
//! 1-9 and 0 jump to a section, `/` searches, `?` shows every key. Slow work runs
//! on a background thread so the screen never freezes.

mod account;
mod downloads;
mod library;
mod modal;
mod recordings;
mod search;
mod sidebar;
mod status;
mod theme;
mod watch;
mod youtube;

use crate::config::Config;
use crate::db::{Db, Episode};
use crate::youtube::Video;
use crate::{downloads as dl, library as media, play, record as rec, restream, runs, schedules, status as st};
use anyhow::Result;
use modal::{Action, Modal};
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Margin, Rect};
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, HighlightSpacing, List, ListItem, ListState};
use ratatui::{DefaultTerminal, Frame};
use serde_json::json;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::{Duration, Instant};
use theme::*;

const REFRESH_EVERY: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, PartialEq, Debug)]
enum Section {
    Search,
    Live,
    Movies,
    Series,
    YouTube,
    Recordings,
    Downloads,
    Library,
    Account,
    Status,
}

const SECTIONS: [Section; 10] = [
    Section::Search,
    Section::Live,
    Section::Movies,
    Section::Series,
    Section::YouTube,
    Section::Recordings,
    Section::Downloads,
    Section::Library,
    Section::Account,
    Section::Status,
];

#[derive(Clone, Copy, PartialEq)]
enum Focus {
    Sidebar,
    List,
}

/// What a background task hands back to the screen (errors already redacted).
type TaskResult = Result<Done, String>;

enum Done {
    Message(String),
    Episodes { series_id: i64, episodes: Vec<Episode> },
    YouTube { query: String, videos: Vec<Video>, for_search: bool },
}

struct App {
    cfg: Config,
    db: Db,
    section: Section,
    focus: Focus,
    search: search::Search,
    watch: watch::Watch,
    youtube: youtube::YouTube,
    recordings_list: ListState,
    downloads_list: ListState,
    library_list: ListState,
    show_password: bool,
    status: st::Status,
    schedules: Vec<schedules::Schedule>,
    runs: Vec<runs::Run>,
    jobs: Vec<dl::Job>,
    media: Vec<media::Media>,
    favorites: usize,
    refreshed: Instant,
    message: Option<(String, Color, Instant)>,
    modal: Option<Modal>,
    tasks: (Sender<TaskResult>, Receiver<TaskResult>),
    running: usize,
    /// Set by `e` in Account: leave the screen, open the .env in an editor, come back.
    edit_env: bool,
    quit: bool,
}

pub fn run(cfg: &Config) -> Result<()> {
    let mut app = App::new(cfg.clone(), Db::open(&cfg.data_dir)?);
    crate::log::event(cfg, "tui.start", json!({ "profile": cfg.profile }));
    let mut terminal = ratatui::init();
    let result = app.event_loop(&mut terminal);
    ratatui::restore();
    result
}

impl App {
    fn new(cfg: Config, db: Db) -> App {
        let mut app = App {
            status: st::collect(&cfg, Some(&db)),
            schedules: schedules::list(&cfg.data_dir),
            runs: runs::recent(&cfg.data_dir, 12),
            jobs: dl::list(&cfg),
            media: media::list(&cfg),
            favorites: crate::favorites::live(&cfg.data_dir).len(),
            search: search::Search::default(),
            watch: watch::Watch::default(),
            youtube: youtube::YouTube::default(),
            cfg,
            db,
            section: Section::Live,
            focus: Focus::List,
            recordings_list: ListState::default().with_selected(Some(0)),
            downloads_list: ListState::default().with_selected(Some(0)),
            library_list: ListState::default().with_selected(Some(0)),
            show_password: false,
            refreshed: Instant::now(),
            message: None,
            modal: None,
            tasks: channel(),
            running: 0,
            edit_env: false,
            quit: false,
        };
        app.watch.reset(&app.db, &app.cfg);
        app
    }

    fn event_loop(&mut self, terminal: &mut DefaultTerminal) -> Result<()> {
        while !self.quit {
            self.watch.mark_episodes(&self.cfg, &self.jobs);
            terminal.draw(|f| self.draw(f))?;
            let busy = self.running > 0 || self.jobs.iter().any(|j| j.state == dl::State::Downloading);
            let wait = if busy { Duration::from_millis(250) } else { REFRESH_EVERY.saturating_sub(self.refreshed.elapsed()).min(Duration::from_secs(5)) };
            if event::poll(wait)? {
                if let Event::Key(key) = event::read()?
                    && key.kind == KeyEventKind::Press
                {
                    self.on_key(key);
                }
            } else if self.refreshed.elapsed() >= REFRESH_EVERY {
                self.refresh();
            } else if busy {
                self.jobs = dl::list(&self.cfg);
            }
            self.collect_tasks();
            if self.edit_env {
                self.edit_env = false;
                ratatui::restore();
                let editor = std::env::var("EDITOR").unwrap_or_else(|_| "nano".into());
                let _ = std::process::Command::new(editor).arg(&self.cfg.env_file).status();
                *terminal = ratatui::init();
                if let Ok(cfg) = Config::load(self.cfg.file.as_deref()) {
                    self.cfg = cfg;
                }
                self.say("Login file saved and reloaded", OK);
            }
        }
        Ok(())
    }

    fn refresh(&mut self) {
        self.status = st::collect(&self.cfg, Some(&self.db));
        self.schedules = schedules::list(&self.cfg.data_dir);
        self.runs = runs::recent(&self.cfg.data_dir, 12);
        self.jobs = dl::list(&self.cfg);
        self.media = media::list(&self.cfg);
        self.refreshed = Instant::now();
        self.watch.refresh_now(&self.db);
    }

    fn say(&mut self, text: impl Into<String>, color: Color) {
        self.message = Some((text.into(), color, Instant::now()));
    }

    fn fail(&mut self, text: impl Into<String>) {
        self.modal = Some(Modal::message("That didn't work", text.into(), BAD));
    }

    // ── background work ──────────────────────────────────────────────────

    fn spawn(&mut self, work: impl FnOnce(&Config) -> anyhow::Result<Done> + Send + 'static) {
        let cfg = self.cfg.clone();
        let tx = self.tasks.0.clone();
        self.running += 1;
        std::thread::spawn(move || {
            let result = work(&cfg).map_err(|e| cfg.provider.redact(&format!("{e:#}")));
            let _ = tx.send(result);
        });
    }

    fn collect_tasks(&mut self) {
        let mut changed = false;
        while let Ok(result) = self.tasks.1.try_recv() {
            self.running = self.running.saturating_sub(1);
            changed = true;
            match result {
                Ok(Done::Message(text)) => self.say(text, OK),
                Ok(Done::Episodes { series_id, episodes }) => self.watch.episodes_loaded(series_id, episodes),
                Ok(Done::YouTube { query, videos, for_search }) => {
                    if for_search {
                        self.search.youtube_loaded(&query, videos);
                    } else {
                        self.youtube.loaded(&query, videos);
                    }
                }
                Err(e) => {
                    self.watch.episodes_failed();
                    self.youtube.failed();
                    self.search.youtube_failed();
                    self.fail(e);
                }
            }
        }
        if changed {
            self.refresh();
        }
    }

    // ── actions ──────────────────────────────────────────────────────────

    /// Run what the user picked. Anything that needs the provider connection while
    /// something else holds it asks first (`confirmed` is true once they said yes).
    fn perform(&mut self, action: Action, confirmed: bool) {
        self.status = st::collect(&self.cfg, Some(&self.db));
        if !confirmed && let Some(question) = self.needs_confirmation(&action) {
            self.modal = Some(Modal::confirm(question, action));
            return;
        }
        match action {
            Action::PlayLive(c) => self.play(&c.name, play::Target::Live(c.stream_id)),
            Action::PlayMovie(m) => match self.db.movie_url(m.stream_id) {
                Ok(Some(url)) => self.play(&m.name, play::Target::Url(&url)),
                _ => self.fail("The provider no longer lists this movie."),
            },
            Action::PlayEpisode { series, episode } => match self.db.episode_url(&episode.episode_id) {
                Ok(Some(url)) => {
                    let title = format!("{series} S{:02}E{:02}", episode.season, episode.episode_num);
                    self.play(&title, play::Target::Url(&url))
                }
                _ => self.fail("The provider no longer lists this episode."),
            },
            Action::PlayFile(m) => self.play(&m.name, play::Target::File(&m.path)),
            Action::PlayYouTube(v) => self.play(&v.title, play::Target::Url(&v.url)),
            Action::RecordMenu(c) => self.modal = Some(Modal::record_menu(&c, &self.watch.guide, &self.schedules)),
            Action::RecordForm(c) => self.modal = Some(Modal::record_form(c, &self.db)),
            Action::ScheduleForm(c) => self.modal = Some(Modal::schedule_form(c)),
            Action::RecordNow { channel, minutes, show } => {
                self.say(format!("Starting the recording of {}…", short(&channel.name)), DIM);
                self.spawn(move |cfg| {
                    let req = rec::Request { stream_id: channel.stream_id, channel_name: &channel.name, minutes, show_name: show.as_deref() };
                    let started = rec::record_now(cfg, &req)?;
                    let extra = started.warning.map(|w| format!(" ({w})")).unwrap_or_default();
                    Ok(Done::Message(format!("Recording {} for {} min{extra}", short(&channel.name), started.minutes)))
                });
            }
            Action::Schedule { channel, time, minutes, days, date, show } => self.spawn(move |cfg| {
                let s = schedules::create(
                    cfg,
                    schedules::New { stream_id: channel.stream_id, channel_name: &channel.name, time: &time, duration_min: minutes, days, date, show_name: show.as_deref(), notify: true, created_by: "kdtv" },
                )?;
                Ok(Done::Message(format!("Scheduled: {}", s.what)))
            }),
            Action::StopRecording(unit) => self.spawn(move |cfg| {
                rec::stop(cfg, &unit)?;
                Ok(Done::Message("Recording stopped; what was recorded is kept.".into()))
            }),
            Action::PauseSchedule { id, enabled } => self.spawn(move |cfg| {
                let s = schedules::set_enabled(cfg, &id, enabled)?;
                Ok(Done::Message(format!("{}: {}", if enabled { "Resumed" } else { "Paused" }, s.what)))
            }),
            Action::DeleteSchedule(id) => self.spawn(move |cfg| {
                schedules::delete(cfg, &id)?;
                Ok(Done::Message("Schedule deleted.".into()))
            }),
            Action::RestreamStart { channel, replace } => {
                // A yes to "switch the restream?" is the go-ahead to replace it.
                let replace = replace || confirmed;
                self.say(format!("Starting the restream of {}…", short(&channel.name)), DIM);
                self.spawn(move |cfg| {
                    let s = restream::start(cfg, &channel, replace)?;
                    Ok(Done::Message(format!("Restreaming {} → {}", short(&s.channel_name), s.watch_url)))
                });
            }
            Action::RestreamStop => self.spawn(|cfg| {
                let name = restream::stop(cfg)?;
                Ok(Done::Message(match name {
                    Some(n) => format!("Stopped the restream of {}.", short(&n)),
                    None => "No restream was running.".into(),
                }))
            }),
            Action::DownloadMovie(m) => self.spawn(move |cfg| {
                dl::add_movie(cfg, &m)?;
                Ok(Done::Message(format!("Queued {} (see Downloads)", m.name)))
            }),
            Action::DownloadEpisodes { series, episodes, .. } => self.spawn(move |cfg| {
                let jobs = dl::add_episodes(cfg, &series, &episodes)?;
                Ok(Done::Message(format!("Queued {} episode(s) of {series} (see Downloads)", jobs.len())))
            }),
            Action::DownloadYouTube { video, audio } => self.spawn(move |cfg| {
                dl::add_youtube(cfg, &video, audio)?;
                Ok(Done::Message(format!("Queued {} (see Downloads)", video.title)))
            }),
            Action::CancelDownload(id) => self.spawn(move |cfg| {
                let job = dl::cancel(cfg, &id)?;
                Ok(Done::Message(format!("Cancelled {}", job.title)))
            }),
            Action::Refresh { guide, everything } => {
                self.say(
                    match (guide, everything) {
                        (_, true) => "Updating everything: channels, guide, movies and series (about a minute)…",
                        (true, _) => "Updating the channel list and TV guide (about 30 s)…",
                        _ => "Updating the channel list (a few seconds)…",
                    },
                    DIM,
                );
                self.spawn(move |cfg| Ok(Done::Message(crate::refresh::run(cfg, crate::refresh::What { guide: guide || everything, everything })?)));
            }
        }
    }

    fn needs_confirmation(&self, action: &Action) -> Option<String> {
        let recording = !self.status.recording.is_empty();
        let busy = self.status.connection_busy();
        let cut = "Your provider allows one connection, so this may cut it. Go ahead anyway?";
        match action {
            Action::PlayLive(_) | Action::PlayMovie(_) | Action::PlayEpisode { .. } if busy => {
                Some(format!("A recording or the restream is using the connection. {cut}"))
            }
            Action::RecordNow { .. } if busy => Some(format!("Something is already recording or restreaming. {cut}")),
            Action::RestreamStart { .. } if recording => Some(format!("A recording is running. {cut}")),
            Action::RestreamStart { replace: false, .. } if self.status.restream.live => {
                let current = self.status.restream.channel_name.as_deref().map(short).unwrap_or_default();
                Some(format!("The family is watching {current}. Switch the restream to this channel?"))
            }
            Action::StopRecording(_) => Some("Stop this recording? What is recorded so far is kept.".into()),
            Action::RestreamStop => Some("Stop the restream? Anyone watching it loses the picture.".into()),
            Action::DeleteSchedule(_) => Some("Delete this schedule? Its timer is removed too.".into()),
            Action::CancelDownload(_) => Some("Cancel this download? The partial file is removed.".into()),
            Action::DownloadEpisodes { episodes, what, .. } if episodes.len() > 1 => {
                Some(format!("Download {what}: {} episodes, one at a time? Each one is a large file.", episodes.len()))
            }
            _ => None,
        }
    }

    fn play(&mut self, title: &str, target: play::Target) {
        match play::play(&self.cfg, title, target) {
            Ok(()) => self.say(format!("Playing {} in mpv", short(title)), OK),
            Err(e) => self.fail(format!("{e:#}")),
        }
    }

    // ── keys ─────────────────────────────────────────────────────────────

    fn on_key(&mut self, key: KeyEvent) {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && matches!(key.code, KeyCode::Char('c') | KeyCode::Char('q')) {
            self.quit = true;
            return;
        }
        if let Some(modal) = self.modal.take() {
            match modal.on_key(key) {
                modal::Outcome::Keep(m) => self.modal = Some(m),
                modal::Outcome::Close => {}
                modal::Outcome::Run(action, confirmed) => self.perform(action, confirmed),
            }
            return;
        }
        // Sections with a search box take the keys while their box is active.
        if self.focus == Focus::List {
            match self.section {
                Section::Search => return self.search_key(key),
                Section::YouTube if self.youtube.typing => return self.youtube_typing_key(key),
                Section::Live | Section::Movies | Section::Series if self.watch.typing => {
                    self.watch.on_typing_key(key, &self.db, &self.cfg);
                    return;
                }
                _ => {}
            }
        }
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Char('?') => self.modal = Some(Modal::help()),
            KeyCode::Char('r') if ctrl => {
                self.refresh();
                self.say("Refreshed", DIM);
            }
            KeyCode::Char('/') => {
                match self.section {
                    Section::Live | Section::Movies | Section::Series => self.watch.typing = true,
                    Section::YouTube => self.youtube.typing = true,
                    _ => self.open_section(Section::Search),
                }
                self.focus = Focus::List;
            }
            KeyCode::Char(c @ '0'..='9') => {
                let index = if c == '0' { 9 } else { c as usize - '1' as usize };
                self.open_section(SECTIONS[index]);
                self.focus = Focus::List;
            }
            KeyCode::Tab | KeyCode::BackTab => {
                self.focus = if self.focus == Focus::Sidebar { Focus::List } else { Focus::Sidebar };
            }
            _ if self.focus == Focus::Sidebar => self.sidebar_key(key),
            _ => self.list_key(key),
        }
    }

    fn sidebar_key(&mut self, key: KeyEvent) {
        let at = SECTIONS.iter().position(|s| *s == self.section).unwrap_or(0);
        match key.code {
            KeyCode::Up | KeyCode::Char('k') => self.open_section(SECTIONS[(at + SECTIONS.len() - 1) % SECTIONS.len()]),
            KeyCode::Down | KeyCode::Char('j') => self.open_section(SECTIONS[(at + 1) % SECTIONS.len()]),
            KeyCode::Right | KeyCode::Enter | KeyCode::Char('l') => self.focus = Focus::List,
            _ => {}
        }
    }

    fn list_key(&mut self, key: KeyEvent) {
        let back = matches!(key.code, KeyCode::Left | KeyCode::Esc | KeyCode::Backspace | KeyCode::Char('h'));
        let action = match self.section {
            Section::Live | Section::Movies | Section::Series => {
                if back {
                    if !self.watch.back(&self.db, &self.cfg) {
                        self.focus = Focus::Sidebar;
                    }
                    None
                } else {
                    self.watch.on_key(key, &self.db, self.status.restream.live)
                }
            }
            _ if back => {
                self.focus = Focus::Sidebar;
                None
            }
            Section::YouTube => self.youtube.on_key(key),
            Section::Recordings => self.recordings_key(key),
            Section::Downloads => self.downloads_key(key),
            Section::Library => self.library_key(key),
            Section::Account => self.account_key(key),
            Section::Search | Section::Status => None,
        };
        if let Some(action) = action {
            self.perform(action, false);
        }
        self.start_pending();
    }

    /// Kick off what a key asked for in the background (episodes, a YouTube search).
    fn start_pending(&mut self) {
        if let Some(series_id) = self.watch.wants_episodes.take() {
            self.spawn(move |cfg| Ok(Done::Episodes { series_id, episodes: crate::episodes(cfg, series_id)? }));
        }
        if let Some(query) = self.youtube.wants_search.take() {
            self.spawn(move |_| Ok(Done::YouTube { videos: crate::youtube::search(&query, 20)?, query, for_search: false }));
        }
        if let Some(query) = self.search.wants_youtube.take() {
            self.spawn(move |_| Ok(Done::YouTube { videos: crate::youtube::search(&query, 8)?, query, for_search: true }));
        }
    }

    fn youtube_typing_key(&mut self, key: KeyEvent) {
        if key.code == KeyCode::Esc && self.youtube.query.is_empty() {
            self.focus = Focus::Sidebar;
        }
        self.youtube.on_typing_key(key);
        self.start_pending();
    }

    fn open_section(&mut self, section: Section) {
        if self.section == section {
            return;
        }
        self.section = section;
        let mode = match section {
            Section::Live => Some(watch::Mode::Live),
            Section::Movies => Some(watch::Mode::Movies),
            Section::Series => Some(watch::Mode::Series),
            _ => None,
        };
        if let Some(mode) = mode {
            self.watch.mode = mode;
            self.watch.query.clear();
            self.watch.typing = false;
            self.watch.reset(&self.db, &self.cfg);
        }
        if section == Section::YouTube && self.youtube.is_empty() {
            self.youtube.typing = true;
        }
    }

    // ── drawing ──────────────────────────────────────────────────────────

    fn draw(&mut self, f: &mut Frame) {
        f.render_widget(Block::new().style(Style::new().bg(BG).fg(FG)), f.area());
        let [body, bar] = Layout::vertical([Constraint::Min(0), Constraint::Length(1)]).areas(f.area().inner(Margin { horizontal: 1, vertical: 0 }));
        let wide = body.width >= 110;
        let [side, list, detail] = Layout::horizontal([Constraint::Length(26), Constraint::Min(30), Constraint::Percentage(if wide { 32 } else { 0 })])
            .spacing(1)
            .areas(body);

        self.draw_sidebar(f, side);
        let focused = self.focus == Focus::List;
        match self.section {
            Section::Search => self.draw_search(f, list, detail, wide),
            Section::Live | Section::Movies | Section::Series => {
                self.watch.draw_list(f, list, focused, &self.status);
                if wide {
                    self.watch.draw_detail(f, detail, &self.schedules, &self.status, &self.cfg.watch_url);
                }
            }
            Section::YouTube => self.youtube.draw(f, list, detail, focused, wide),
            Section::Recordings => self.draw_recordings(f, list, detail, wide),
            Section::Downloads => self.draw_downloads(f, list, detail, wide),
            Section::Library => self.draw_library(f, list, detail, wide),
            Section::Account => self.draw_account(f, list, detail, wide),
            Section::Status => self.draw_status(f, list, detail, wide),
        }
        self.draw_bar(f, bar);
        if let Some(modal) = &mut self.modal {
            modal.draw(f, body);
        }
    }

    fn draw_bar(&mut self, f: &mut Frame, area: Rect) {
        let mut spans: Vec<Span> = vec![];
        let sep = || Span::styled("  │  ", Style::new().fg(BORDER));
        for r in self.status.recording.iter().take(1) {
            spans.push(Span::styled(" REC ", Style::new().fg(BG).bg(BAD).bold()));
            spans.push(Span::raw(format!(" {}", short(&r.what))));
        }
        if self.status.restream.live {
            if !spans.is_empty() {
                spans.push(sep());
            }
            spans.push(Span::styled(" RESTREAMING ", Style::new().fg(BG).bg(OK).bold()));
            spans.push(Span::raw(format!(" {}", short(self.status.restream.channel_name.as_deref().unwrap_or("?")))));
            spans.push(dim("  x in Live stops it"));
        }
        if spans.is_empty() {
            spans.push(dim(match self.next_recording() {
                Some(next) => format!(" next recording {next}"),
                None => " nothing recording".into(),
            }));
        }
        let active: Vec<&dl::Job> = self.jobs.iter().filter(|j| j.unfinished()).collect();
        if let Some(job) = active.iter().find(|j| j.state == dl::State::Downloading) {
            spans.push(sep());
            let progress = job.progress().map(|p| format!("{p:.0}%")).unwrap_or_else(|| media::human_size(job.bytes));
            let more = if active.len() > 1 { format!(" · {} to go", active.len() - 1) } else { String::new() };
            spans.push(Span::styled(format!("↓ {} · {progress}{more}", short(&job.title)), Style::new().fg(ACCENT)));
        }
        if self.running > 0 {
            spans.push(sep());
            spans.push(dim("working…"));
        }
        if let Some((text, color, at)) = &self.message {
            if at.elapsed() < Duration::from_secs(8) {
                spans.push(sep());
                spans.push(Span::styled(text.clone(), Style::new().fg(*color)));
            } else {
                self.message = None;
            }
        }
        let hints = format!("{}  ? keys ", self.hints());
        let [left, right] = Layout::horizontal([Constraint::Min(0), Constraint::Length(hints.chars().count() as u16)]).areas(area);
        f.render_widget(Line::from(spans), left);
        f.render_widget(Line::from(hints).fg(DIM), right);
    }

    /// "18:29 Occupation Double" for the next enabled schedule, if one is planned.
    fn next_recording(&self) -> Option<String> {
        self.schedules
            .iter()
            .filter(|s| s.enabled)
            .filter_map(|s| {
                // "Sun 2026-10-18 18:29:00 EDT": sort on the date and time part.
                let when = s.next_run.as_deref()?;
                let key = when.get(4..20)?.to_string();
                let day = if when.get(4..14)? == chrono::Local::now().format("%Y-%m-%d").to_string() { "" } else { when.get(0..4)? };
                let name = s.show_name.clone().unwrap_or_else(|| short(&s.channel_name));
                Some((key, format!("{day}{} {name}", when.get(15..20)?)))
            })
            .min_by(|a, b| a.0.cmp(&b.0))
            .map(|(_, text)| text)
    }

    fn hints(&self) -> &'static str {
        if self.modal.is_some() {
            return "↵ ok  esc cancel";
        }
        if self.focus == Focus::Sidebar {
            return "↑↓ pick  → open  q quit";
        }
        match self.section {
            Section::Search => "type to search  ↑↓ results  ↵ open  esc menu",
            Section::Live | Section::Movies | Section::Series => self.watch.hints(self.status.restream.live),
            Section::YouTube => self.youtube.hints(),
            Section::Recordings => "x stop  space pause  d delete  ← menu",
            Section::Downloads => "x cancel  ← menu",
            Section::Library => "↵ play  ← menu",
            Section::Account => "u list  g guide  m everything  p password  e edit  ← menu",
            Section::Status => "ctrl+r refresh  ← menu",
        }
    }
}

// ── shared helpers ───────────────────────────────────────────────────────

/// A list with headings; only rows with a key can be selected.
struct Rows<K> {
    items: Vec<(Option<K>, Text<'static>)>,
}

impl<K: Clone> Rows<K> {
    fn new() -> Self {
        Rows { items: vec![] }
    }

    fn header(&mut self, title: &str) {
        if !self.items.is_empty() {
            self.items.push((None, Text::raw("")));
        }
        self.items.push((None, Text::from(heading(title))));
    }

    fn note(&mut self, text: &str) {
        self.items.push((None, Text::from(Line::from(text.to_string()).fg(DIM))));
    }

    fn row(&mut self, key: K, text: impl Into<Text<'static>>) {
        self.items.push((Some(key), text.into()));
    }

    fn keys(&self) -> Vec<K> {
        self.items.iter().filter_map(|(k, _)| k.clone()).collect()
    }

    /// Draw, highlighting the `selected`-th selectable row.
    fn render(self, f: &mut Frame, area: Rect, state: &mut ListState) {
        let count = self.keys().len();
        let selected = state.selected().unwrap_or(0).min(count.saturating_sub(1));
        let item_index = self.items.iter().enumerate().filter(|(_, (k, _))| k.is_some()).nth(selected).map(|(i, _)| i);
        let items: Vec<ListItem> = self.items.into_iter().map(|(_, t)| ListItem::new(t)).collect();
        let list = List::new(items).highlight_style(selected_style()).highlight_spacing(HighlightSpacing::Never);
        let mut view = ListState::default().with_offset(state.offset()).with_selected(item_index);
        f.render_stateful_widget(list, area, &mut view);
        *state = (*state).with_offset(view.offset()).with_selected(Some(selected));
    }
}

fn selected_style() -> Style {
    Style::new().bg(SELECT_BG).fg(ACCENT).bold()
}

/// ↑/↓/j/k/PgUp/PgDn on a list of `count` selectable rows.
fn move_in(state: &mut ListState, key: KeyCode, count: usize) -> bool {
    let current = state.selected().unwrap_or(0) as isize;
    let delta: isize = match key {
        KeyCode::Up | KeyCode::Char('k') => -1,
        KeyCode::Down | KeyCode::Char('j') => 1,
        KeyCode::PageUp => -10,
        KeyCode::PageDown => 10,
        KeyCode::Home | KeyCode::Char('g') => -100_000,
        KeyCode::End | KeyCode::Char('G') => 100_000,
        _ => return false,
    };
    let last = count.saturating_sub(1) as isize;
    state.select(Some((current + delta).clamp(0, last.max(0)) as usize));
    true
}

fn label_value(label: &str, value: String, color: Color) -> Line<'static> {
    Line::from(vec![Span::styled(format!("{label:<16}"), Style::new().fg(DIM)), Span::styled(value, Style::new().fg(color))])
}

fn hhmm(ts: i64) -> String {
    use chrono::TimeZone;
    chrono::Local.timestamp_opt(ts, 0).single().map(|t| t.format("%H:%M").to_string()).unwrap_or_default()
}

/// A short, readable name: game channels become their game, descriptions their first part.
fn short(what: &str) -> String {
    let text = match crate::db::event_info(what) {
        Some(e) => format!("{} · {}", e.channel, e.title),
        None => what.split(',').next().unwrap_or(what).trim().to_string(),
    };
    if text.chars().count() > 34 { format!("{}…", text.chars().take(33).collect::<String>()) } else { text }
}
