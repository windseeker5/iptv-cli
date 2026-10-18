//! The full-screen interface: search first, four tabs.
//!
//! Watch (live, movies, series) · Record (now, scheduled, recent) · Library
//! (downloads, files) · Status. Slow work (starting a recording, a restream,
//! fetching episodes) runs on a background thread so the screen never freezes.
//!
//! Global keys: `/` search, 1-4 or Tab switch tabs, ctrl+r refresh, q quit.

mod library;
mod modal;
mod record;
mod status;
mod watch;

use crate::config::Config;
use crate::db::{Db, Episode};
use crate::{downloads, library as media, play, record as rec, restream, runs, schedules, status as st};
use anyhow::Result;
use modal::{Action, Modal};
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Margin, Rect};
use ratatui::style::{Color, Modifier, Style, Stylize};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{HighlightSpacing, List, ListItem, ListState};
use ratatui::{DefaultTerminal, Frame};
use serde_json::json;
use std::sync::mpsc::{channel, Receiver, Sender};
use std::time::{Duration, Instant};

pub const ACCENT: Color = Color::Cyan;
pub const DIM: Color = Color::DarkGray;
pub const OK: Color = Color::Green;
pub const WARN: Color = Color::Yellow;
pub const BAD: Color = Color::Red;
const REFRESH_EVERY: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    Watch,
    Record,
    Library,
    Status,
}

const TABS: [(Tab, &str); 4] = [(Tab::Watch, "Watch"), (Tab::Record, "Record"), (Tab::Library, "Library"), (Tab::Status, "Status")];

/// What a background task hands back to the screen (errors already redacted).
type TaskResult = Result<Done, String>;

enum Done {
    Message(String),
    Episodes { series_id: i64, episodes: Vec<Episode> },
}

struct App {
    cfg: Config,
    db: Db,
    tab: Tab,
    watch: watch::Watch,
    record_list: ListState,
    library_list: ListState,
    status: st::Status,
    schedules: Vec<schedules::Schedule>,
    runs: Vec<runs::Run>,
    jobs: Vec<downloads::Job>,
    media: Vec<media::Media>,
    refreshed: Instant,
    message: Option<(String, Color)>,
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
            runs: runs::recent(&cfg.data_dir, 8),
            jobs: downloads::list(&cfg),
            media: vec![],
            watch: watch::Watch::default(),
            cfg,
            db,
            tab: Tab::Watch,
            record_list: ListState::default().with_selected(Some(0)),
            library_list: ListState::default().with_selected(Some(0)),
            refreshed: Instant::now(),
            message: None,
            modal: None,
            tasks: channel(),
            running: 0,
            quit: false,
        };
        app.media = media::list(&app.cfg);
        app.watch.reset(&app.db, &app.cfg);
        app
    }

    fn event_loop(&mut self, terminal: &mut DefaultTerminal) -> Result<()> {
        while !self.quit {
            terminal.draw(|f| self.draw(f))?;
            let wait = if self.running > 0 || self.jobs_active() {
                Duration::from_millis(250)
            } else {
                REFRESH_EVERY.saturating_sub(self.refreshed.elapsed())
            };
            if event::poll(wait)? {
                if let Event::Key(key) = event::read()?
                    && key.kind == KeyEventKind::Press
                {
                    self.on_key(key);
                }
            } else if self.refreshed.elapsed() >= REFRESH_EVERY {
                self.refresh();
            } else if self.jobs_active() {
                self.jobs = downloads::list(&self.cfg);
            }
            self.collect_tasks();
        }
        Ok(())
    }

    fn jobs_active(&self) -> bool {
        self.jobs.iter().any(|j| matches!(j.state, downloads::State::Downloading))
    }

    fn refresh(&mut self) {
        self.status = st::collect(&self.cfg, Some(&self.db));
        self.schedules = schedules::list(&self.cfg.data_dir);
        self.runs = runs::recent(&self.cfg.data_dir, 8);
        self.jobs = downloads::list(&self.cfg);
        self.media = media::list(&self.cfg);
        self.refreshed = Instant::now();
        self.watch.load_guide(&self.db);
    }

    fn say(&mut self, text: impl Into<String>, color: Color) {
        self.message = Some((text.into(), color));
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
                    self.say(e, BAD)
                }
            }
        }
        if changed {
            self.refresh();
        }
    }

    // ── actions ──────────────────────────────────────────────────────────

    /// Run what the user picked. Anything that needs the provider connection while a
    /// recording holds it asks first (`confirmed` is true once they said yes).
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
                _ => self.say("The provider no longer lists this movie.", BAD),
            },
            Action::PlayEpisode { series, episode } => match self.db.episode_url(&episode.episode_id) {
                Ok(Some(url)) => {
                    let title = format!("{series} S{:02}E{:02} {}", episode.season, episode.episode_num, episode.title);
                    self.play(&title, play::Target::Url(&url))
                }
                _ => self.say("The provider no longer lists this episode.", BAD),
            },
            Action::PlayFile(m) => self.play(&m.name, play::Target::File(&m.path)),
            Action::RecordMenu(c) => self.modal = Some(Modal::record_menu(&c, &self.watch.guide)),
            Action::RecordForm(c) => self.modal = Some(Modal::record_form(c, &self.db)),
            Action::ScheduleForm(c) => self.modal = Some(Modal::schedule_form(c)),
            Action::RecordNow { channel, minutes, show } => {
                self.say(format!("Starting the recording of {}…", channel.name), DIM);
                self.spawn(move |cfg| {
                    let req = rec::Request { stream_id: channel.stream_id, channel_name: &channel.name, minutes, show_name: show.as_deref() };
                    let started = rec::record_now(cfg, &req)?;
                    let extra = started.warning.map(|w| format!(" ({w})")).unwrap_or_default();
                    Ok(Done::Message(format!("Recording {} for {minutes} min{extra}", channel.name)))
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
                Ok(Done::Message("Recording stopped; the file so far is kept.".into()))
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
                Ok(Done::Message(format!("Queued {} (Library tab)", m.name)))
            }),
            Action::DownloadEpisodes { series, episodes } => self.spawn(move |cfg| {
                let jobs = downloads::add_episodes(cfg, &series, &episodes)?;
                Ok(Done::Message(format!("Queued {} episode(s) of {series} (Library tab)", jobs.len())))
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
            Err(e) => self.say(format!("{e:#}"), BAD),
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
        self.message = None;

        if self.tab == Tab::Watch && self.watch.typing {
            self.watch.on_typing_key(key, &self.db, &self.cfg);
            return;
        }
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Char('r') if ctrl => {
                self.refresh();
                self.say("Refreshed", DIM);
            }
            KeyCode::Char('/') => {
                self.tab = Tab::Watch;
                self.watch.typing = true;
            }
            KeyCode::Char('1') => self.tab = Tab::Watch,
            KeyCode::Char('2') => self.tab = Tab::Record,
            KeyCode::Char('3') => self.tab = Tab::Library,
            KeyCode::Char('4') => self.tab = Tab::Status,
            KeyCode::Tab => self.tab = TABS[(self.tab_index() + 1) % TABS.len()].0,
            KeyCode::BackTab => self.tab = TABS[(self.tab_index() + TABS.len() - 1) % TABS.len()].0,
            _ => {
                let action = match self.tab {
                    Tab::Watch => self.watch.on_key(key, &self.db, &self.cfg),
                    Tab::Record => self.record_key(key),
                    Tab::Library => self.library_key(key),
                    Tab::Status => None,
                };
                if let Some(action) = action {
                    self.perform(action, false);
                }
                if let Some(series_id) = self.watch.wants_episodes.take() {
                    self.spawn(move |cfg| Ok(Done::Episodes { series_id, episodes: crate::episodes(cfg, series_id)? }));
                }
            }
        }
    }

    fn tab_index(&self) -> usize {
        TABS.iter().position(|(t, _)| *t == self.tab).unwrap_or(0)
    }

    // ── drawing ──────────────────────────────────────────────────────────

    fn draw(&mut self, f: &mut Frame) {
        let [header, body, footer] =
            Layout::vertical([Constraint::Length(2), Constraint::Min(0), Constraint::Length(1)]).areas(f.area());
        let body = body.inner(Margin { horizontal: 1, vertical: 0 });
        self.draw_header(f, header);
        match self.tab {
            Tab::Watch => self.watch.draw(f, body, &self.status),
            Tab::Record => self.draw_record(f, body),
            Tab::Library => self.draw_library(f, body),
            Tab::Status => self.draw_status(f, body),
        }
        self.draw_footer(f, footer);
        if let Some(modal) = &mut self.modal {
            modal.draw(f, body);
        }
    }

    fn draw_header(&self, f: &mut Frame, area: Rect) {
        let mut spans = vec![Span::styled(" kd ", Style::new().fg(Color::Black).bg(ACCENT).bold()), Span::raw("   ")];
        for (i, (tab, name)) in TABS.iter().enumerate() {
            let style = if *tab == self.tab { Style::new().bold().underlined() } else { Style::new().fg(DIM) };
            spans.push(Span::styled(format!("{} {name}", i + 1), style));
            spans.push(Span::raw("   "));
        }
        let [left, right] = Layout::horizontal([Constraint::Min(0), Constraint::Length(24)]).areas(area);
        f.render_widget(Line::from(spans), left);
        let clock = chrono::Local::now().format("%a %H:%M").to_string();
        f.render_widget(Line::from(format!("{}  ·  {clock} ", self.cfg.profile)).fg(DIM).right_aligned(), right);
    }

    fn draw_footer(&self, f: &mut Frame, area: Rect) {
        let mut spans = vec![Span::raw(" ")];
        for r in &self.status.recording {
            spans.push(Span::styled(format!("● REC {}  ", short(&r.what)), Style::new().fg(BAD).bold()));
        }
        if self.status.restream.live {
            let name = self.status.restream.channel_name.as_deref().unwrap_or("?");
            spans.push(Span::styled(format!("● LIVE {}  ", short(name)), Style::new().fg(OK).bold()));
        }
        if let Some(job) = self.jobs.iter().find(|j| j.state == downloads::State::Downloading) {
            spans.push(Span::styled(format!("↓ {} {}  ", short(&job.title), media::human_size(job.bytes)), Style::new().fg(ACCENT)));
        }
        if self.running > 0 {
            spans.push(Span::styled("working…  ", Style::new().fg(DIM)));
        }
        if let Some((text, color)) = &self.message {
            spans.push(Span::styled(text.clone(), Style::new().fg(*color)));
        }
        let hints = self.hints();
        let [left, right] =
            Layout::horizontal([Constraint::Min(0), Constraint::Length(hints.chars().count() as u16)]).areas(area);
        f.render_widget(Line::from(spans), left);
        f.render_widget(Line::from(hints).fg(DIM), right);
    }

    fn hints(&self) -> String {
        if self.modal.is_some() {
            return "↵ ok  esc cancel ".into();
        }
        let keys = match self.tab {
            Tab::Watch => self.watch.hints(),
            Tab::Record => "x stop  space pause  d delete",
            Tab::Library => "↵ play  x cancel",
            Tab::Status => "ctrl+r refresh",
        };
        if self.tab == Tab::Watch && self.watch.typing {
            format!("{keys} ")
        } else {
            format!("{keys}  1-4 tabs  q quit ")
        }
    }
}

// ── shared helpers ───────────────────────────────────────────────────────

/// A list with section headings; only rows with a key can be selected.
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
        self.items.push((None, Text::from(section(title))));
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
        let list = List::new(items)
            .highlight_style(Style::new().fg(ACCENT).bold())
            .highlight_symbol("▌ ")
            .highlight_spacing(HighlightSpacing::Always);
        let mut view = ListState::default().with_offset(state.offset()).with_selected(item_index);
        f.render_stateful_widget(list, area, &mut view);
        *state = (*state).with_offset(view.offset()).with_selected(Some(selected));
    }
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

fn section(title: &str) -> Line<'static> {
    Line::from(title.to_string()).fg(DIM).add_modifier(Modifier::BOLD)
}

fn row(label: &str, value: String, color: Color) -> Line<'static> {
    Line::from(vec![Span::styled(format!("{label:<11}"), Style::new().fg(DIM)), Span::styled(value, Style::new().fg(color))])
}

fn hhmm(ts: i64) -> String {
    use chrono::TimeZone;
    chrono::Local.timestamp_opt(ts, 0).single().map(|t| t.format("%H:%M").to_string()).unwrap_or_default()
}

/// The first part of a description ("NOOVO, every Sun…" → "NOOVO"), at most 28 characters.
fn short(what: &str) -> String {
    let first = what.split(',').next().unwrap_or(what);
    if first.chars().count() > 28 { format!("{}…", first.chars().take(27).collect::<String>()) } else { first.to_string() }
}
