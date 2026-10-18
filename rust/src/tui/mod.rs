//! The full-screen interface, laid out like design option B:
//!
//! ```text
//! ╭ sidebar ╮╭ list ─────────────────╮╭ details ───────╮
//! │ Live    ││ what's in the section ││ the selection  │
//! │ Movies  ││                       ││ and its        │
//! │ …       ││                       ││ actions        │
//! ╰─────────╯╰───────────────────────╯╰────────────────╯
//!  REC …  │  ↓ download  │  message            keys
//! ```
//!
//! ↑↓ move, → or ↵ go into the list, ← or esc go back, Tab switches panel,
//! 1-6 jump to a section, `/` search, `?` all keys, q quit. Slow work runs on a
//! background thread so the screen never freezes.

mod library;
mod modal;
mod record;
mod sidebar;
mod status;
mod theme;
mod watch;

use crate::config::Config;
use crate::db::{Db, Episode};
use crate::{downloads, library as media, play, record as rec, restream, runs, schedules, status as st};
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
    Live,
    Movies,
    Series,
    Record,
    Library,
    Status,
}

const SECTIONS: [Section; 6] = [Section::Live, Section::Movies, Section::Series, Section::Record, Section::Library, Section::Status];

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
}

struct App {
    cfg: Config,
    db: Db,
    section: Section,
    focus: Focus,
    watch: watch::Watch,
    record_list: ListState,
    library_list: ListState,
    status: st::Status,
    schedules: Vec<schedules::Schedule>,
    runs: Vec<runs::Run>,
    jobs: Vec<downloads::Job>,
    media: Vec<media::Media>,
    favorites: usize,
    refreshed: Instant,
    message: Option<(String, Color, Instant)>,
    modal: Option<Modal>,
    tasks: (Sender<TaskResult>, Receiver<TaskResult>),
    running: usize,
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
            jobs: downloads::list(&cfg),
            media: media::list(&cfg),
            favorites: crate::favorites::live(&cfg.data_dir).len(),
            watch: watch::Watch::default(),
            cfg,
            db,
            section: Section::Live,
            focus: Focus::List,
            record_list: ListState::default().with_selected(Some(0)),
            library_list: ListState::default().with_selected(Some(0)),
            refreshed: Instant::now(),
            message: None,
            modal: None,
            tasks: channel(),
            running: 0,
            quit: false,
        };
        app.watch.reset(&app.db, &app.cfg);
        app
    }

    fn event_loop(&mut self, terminal: &mut DefaultTerminal) -> Result<()> {
        while !self.quit {
            terminal.draw(|f| self.draw(f))?;
            let busy = self.running > 0 || self.jobs.iter().any(|j| j.state == downloads::State::Downloading);
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
                self.jobs = downloads::list(&self.cfg);
            }
            self.collect_tasks();
        }
        Ok(())
    }

    fn refresh(&mut self) {
        self.status = st::collect(&self.cfg, Some(&self.db));
        self.schedules = schedules::list(&self.cfg.data_dir);
        self.runs = runs::recent(&self.cfg.data_dir, 12);
        self.jobs = downloads::list(&self.cfg);
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
                Err(e) => {
                    self.watch.episodes_failed();
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
                    let title = format!("{series} S{:02}E{:02} {}", episode.season, episode.episode_num, episode.title);
                    self.play(&title, play::Target::Url(&url))
                }
                _ => self.fail("The provider no longer lists this episode."),
            },
            Action::PlayFile(m) => self.play(&m.name, play::Target::File(&m.path)),
            Action::RecordMenu(c) => self.modal = Some(Modal::record_menu(&c, &self.watch.guide, &self.schedules)),
            Action::RecordForm(c) => self.modal = Some(Modal::record_form(c, &self.db)),
            Action::ScheduleForm(c) => self.modal = Some(Modal::schedule_form(c)),
            Action::RecordNow { channel, minutes, show } => {
                self.say(format!("Starting the recording of {}…", channel.name), DIM);
                self.spawn(move |cfg| {
                    let req = rec::Request { stream_id: channel.stream_id, channel_name: &channel.name, minutes, show_name: show.as_deref() };
                    let started = rec::record_now(cfg, &req)?;
                    let extra = started.warning.map(|w| format!(" ({w})")).unwrap_or_default();
                    Ok(Done::Message(format!("Recording {} for {} min{extra}", channel.name, started.minutes)))
                });
            }
            Action::Schedule { channel, time, minutes, days, date, show } => self.spawn(move |cfg| {
                let s = schedules::create(
                    cfg,
                    schedules::New {
                        stream_id: channel.stream_id,
                        channel_name: &channel.name,
                        time: &time,
                        duration_min: minutes,
                        days,
                        date,
                        show_name: show.as_deref(),
                        created_by: "kd",
                    },
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
                self.say(format!("Starting the restream of {}…", channel.name), DIM);
                self.spawn(move |cfg| {
                    let s = restream::start(cfg, &channel, replace)?;
                    Ok(Done::Message(format!("Restreaming {} → {}", s.channel_name, s.watch_url)))
                });
            }
            Action::RestreamStop => self.spawn(|cfg| {
                let name = restream::stop(cfg)?;
                Ok(Done::Message(match name {
                    Some(n) => format!("Stopped the restream of {n}."),
                    None => "No restream was running.".into(),
                }))
            }),
            Action::DownloadMovie(m) => self.spawn(move |cfg| {
                downloads::add_movie(cfg, &m)?;
                Ok(Done::Message(format!("Queued {} — see Library", m.name)))
            }),
            Action::DownloadEpisodes { series, episodes } => self.spawn(move |cfg| {
                let jobs = downloads::add_episodes(cfg, &series, &episodes)?;
                Ok(Done::Message(format!("Queued {} episode(s) of {series} — see Library", jobs.len())))
            }),
            Action::CancelDownload(id) => self.spawn(move |cfg| {
                let job = downloads::cancel(cfg, &id)?;
                Ok(Done::Message(format!("Cancelled {}", job.title)))
            }),
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
                let current = self.status.restream.channel_name.as_deref().unwrap_or("another channel");
                Some(format!("The family is watching {current}. Switch the restream to this channel?"))
            }
            Action::StopRecording(_) => Some("Stop this recording? What is recorded so far is kept.".into()),
            Action::RestreamStop => Some("Stop the restream? Anyone watching it loses the picture.".into()),
            Action::DeleteSchedule(_) => Some("Delete this schedule? Its timer is removed too.".into()),
            Action::CancelDownload(_) => Some("Cancel this download? The partial file is removed.".into()),
            _ => None,
        }
    }

    fn play(&mut self, title: &str, target: play::Target) {
        match play::play(&self.cfg, title, target) {
            Ok(()) => self.say(format!("Playing {title} in mpv"), OK),
            Err(e) => self.fail(format!("{e:#}")),
        }
    }

    // ── keys ─────────────────────────────────────────────────────────────

    fn on_key(&mut self, key: KeyEvent) {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && key.code == KeyCode::Char('c') {
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
        if self.watch.typing {
            self.watch.on_typing_key(key, &self.db, &self.cfg);
            return;
        }
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Char('?') => self.modal = Some(Modal::help()),
            KeyCode::Char('r') if ctrl => {
                self.refresh();
                self.say("Refreshed", DIM);
            }
            KeyCode::Char('/') => {
                if !self.browsing() {
                    self.open_section(Section::Live);
                }
                self.focus = Focus::List;
                self.watch.typing = true;
            }
            KeyCode::Char(c @ '1'..='6') => {
                self.open_section(SECTIONS[c as usize - '1' as usize]);
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
                    self.watch.on_key(key, &self.db)
                }
            }
            _ if back => {
                self.focus = Focus::Sidebar;
                None
            }
            Section::Record => self.record_key(key),
            Section::Library => self.library_key(key),
            Section::Status => None,
        };
        if let Some(action) = action {
            self.perform(action, false);
        }
        if let Some(series_id) = self.watch.wants_episodes.take() {
            self.spawn(move |cfg| Ok(Done::Episodes { series_id, episodes: crate::episodes(cfg, series_id)? }));
        }
    }

    fn browsing(&self) -> bool {
        matches!(self.section, Section::Live | Section::Movies | Section::Series)
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
            self.watch.reset(&self.db, &self.cfg);
        }
    }

    // ── drawing ──────────────────────────────────────────────────────────

    fn draw(&mut self, f: &mut Frame) {
        f.render_widget(Block::new().style(Style::new().bg(BG).fg(FG)), f.area());
        let [body, bar] = Layout::vertical([Constraint::Min(0), Constraint::Length(1)]).areas(f.area().inner(Margin { horizontal: 1, vertical: 0 }));
        let wide = body.width >= 110;
        let [side, list, detail] = Layout::horizontal([
            Constraint::Length(24),
            Constraint::Min(30),
            Constraint::Percentage(if wide { 34 } else { 0 }),
        ])
        .spacing(1)
        .areas(body);

        self.draw_sidebar(f, side);
        let list_focused = self.focus == Focus::List;
        match self.section {
            Section::Live | Section::Movies | Section::Series => {
                self.watch.draw_list(f, list, list_focused);
                if wide {
                    self.watch.draw_detail(f, detail, &self.schedules);
                }
            }
            Section::Record => self.draw_record(f, list, detail, wide),
            Section::Library => self.draw_library(f, list, detail, wide),
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
        match self.status.recording.first() {
            Some(r) => {
                spans.push(Span::styled(" REC ", Style::new().fg(BG).bg(BAD).bold()));
                spans.push(Span::raw(format!(" {}", short(&r.what))));
                if self.status.recording.len() > 1 {
                    spans.push(dim(format!(" +{}", self.status.recording.len() - 1)));
                }
            }
            None => match self.next_recording() {
                Some(next) => spans.push(dim(format!(" next rec {next}"))),
                None => spans.push(dim(" nothing recording")),
            },
        }
        if self.status.restream.live {
            spans.push(sep());
            spans.push(Span::styled(" LIVE ", Style::new().fg(BG).bg(OK).bold()));
            spans.push(Span::raw(format!(" {}", short(self.status.restream.channel_name.as_deref().unwrap_or("?")))));
        }
        if let Some(job) = self.jobs.iter().find(|j| j.state == downloads::State::Downloading) {
            spans.push(sep());
            spans.push(Span::styled(format!("↓ {} · {}", short(&job.title), media::human_size(job.bytes)), Style::new().fg(ACCENT)));
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
        let hints = format!("{}  ? keys  q quit ", self.hints());
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
            return "↑↓ section  → open  / search";
        }
        match self.section {
            Section::Live | Section::Movies | Section::Series => self.watch.hints(),
            Section::Record => "x stop  space pause  d delete  ← back",
            Section::Library => "↵ play  x cancel  ← back",
            Section::Status => "ctrl+r refresh  ← back",
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
    Line::from(vec![Span::styled(format!("{label:<12}"), Style::new().fg(DIM)), Span::styled(value, Style::new().fg(color))])
}

fn hhmm(ts: i64) -> String {
    use chrono::TimeZone;
    chrono::Local.timestamp_opt(ts, 0).single().map(|t| t.format("%H:%M").to_string()).unwrap_or_default()
}

/// The first part of a description ("NOOVO, every Sun…" → "NOOVO"), at most 30 characters.
fn short(what: &str) -> String {
    let first = what.split(',').next().unwrap_or(what).trim();
    if first.chars().count() > 30 { format!("{}…", first.chars().take(29).collect::<String>()) } else { first.to_string() }
}
