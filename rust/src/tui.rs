//! The full-screen interface: search first, three tabs (Watch, Record, Status).
//!
//! Keys: `/` search from anywhere, ↑↓ or j/k move, ↵ play, 1-3 or Tab switch tabs,
//! `r` refresh, `q` quit. Status refreshes on its own every 30 seconds.

use crate::config::Config;
use crate::db::{Channel, Db, Program};
use crate::{favorites, play, runs, schedules, status};
use anyhow::Result;
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{HighlightSpacing, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::{DefaultTerminal, Frame};
use serde_json::json;
use std::time::{Duration, Instant};

const ACCENT: Color = Color::Cyan;
const DIM: Color = Color::DarkGray;
const OK: Color = Color::Green;
const WARN: Color = Color::Yellow;
const BAD: Color = Color::Red;
const REFRESH_EVERY: Duration = Duration::from_secs(30);

#[derive(Clone, Copy, PartialEq)]
enum Tab {
    Watch,
    Record,
    Status,
}

const TABS: [(Tab, &str); 3] = [(Tab::Watch, "Watch"), (Tab::Record, "Record"), (Tab::Status, "Status")];

struct App<'a> {
    cfg: &'a Config,
    db: Db,
    tab: Tab,
    query: String,
    typing: bool,
    channels: Vec<Channel>,
    list: ListState,
    guide: Vec<Program>,
    status: status::Status,
    schedules: Vec<schedules::Schedule>,
    runs: Vec<runs::Run>,
    refreshed: Instant,
    message: Option<(String, Color)>,
    /// Channel waiting for a second ↵ because a recording holds the connection.
    confirm_play: Option<i64>,
    quit: bool,
}

pub fn run(cfg: &Config) -> Result<()> {
    let mut app = App::new(cfg, Db::open(&cfg.data_dir)?);
    crate::log::event(cfg, "tui.start", json!({ "profile": cfg.profile }));
    let mut terminal = ratatui::init();
    let result = app.event_loop(&mut terminal);
    ratatui::restore();
    result
}

impl<'a> App<'a> {
    fn new(cfg: &'a Config, db: Db) -> App<'a> {
        let status = status::collect(cfg, Some(&db));
        let mut app = App {
            cfg,
            db,
            tab: Tab::Watch,
            query: String::new(),
            typing: false,
            channels: vec![],
            list: ListState::default(),
            guide: vec![],
            status,
            schedules: schedules::list(&cfg.data_dir),
            runs: runs::recent(&cfg.data_dir, 8),
            refreshed: Instant::now(),
            message: None,
            confirm_play: None,
            quit: false,
        };
        app.update_channels();
        app
    }

    fn event_loop(&mut self, terminal: &mut DefaultTerminal) -> Result<()> {
        while !self.quit {
            terminal.draw(|f| self.draw(f))?;
            if event::poll(REFRESH_EVERY.saturating_sub(self.refreshed.elapsed()))? {
                if let Event::Key(key) = event::read()?
                    && key.kind == KeyEventKind::Press
                {
                    self.on_key(key);
                }
            } else {
                self.refresh();
            }
        }
        Ok(())
    }

    fn refresh(&mut self) {
        self.status = status::collect(self.cfg, Some(&self.db));
        self.schedules = schedules::list(&self.cfg.data_dir);
        self.runs = runs::recent(&self.cfg.data_dir, 8);
        self.refreshed = Instant::now();
        self.load_guide();
    }

    // ── state ────────────────────────────────────────────────────────────

    /// Favorites when the search is empty, otherwise the matches.
    fn update_channels(&mut self) {
        self.channels = if self.query.trim().is_empty() {
            favorites::live(&self.cfg.data_dir)
        } else {
            self.db.search_channels(&self.query, 200).unwrap_or_default()
        };
        self.list.select((!self.channels.is_empty()).then_some(0));
        self.load_guide();
    }

    fn selected(&self) -> Option<&Channel> {
        self.list.selected().and_then(|i| self.channels.get(i))
    }

    fn move_selection(&mut self, delta: isize) {
        if self.channels.is_empty() {
            return;
        }
        let current = self.list.selected().unwrap_or(0) as isize;
        let next = (current + delta).clamp(0, self.channels.len() as isize - 1);
        self.list.select(Some(next as usize));
        self.load_guide();
    }

    fn load_guide(&mut self) {
        let now = chrono::Utc::now().timestamp();
        self.guide = match self.selected() {
            Some(c) => self.db.guide(c.stream_id, now, 5).unwrap_or_default(),
            None => vec![],
        };
    }

    fn play_selected(&mut self, confirmed: Option<i64>) {
        let Some(channel) = self.selected().cloned() else { return };
        // Check right now, not 30 seconds ago: the provider allows a single connection.
        self.status = status::collect(self.cfg, Some(&self.db));
        if self.status.connection_busy() && confirmed != Some(channel.stream_id) {
            self.confirm_play = Some(channel.stream_id);
            self.message = Some((
                "A recording is using the provider connection. Press ↵ again to play anyway (it may cut the recording).".into(),
                WARN,
            ));
            return;
        }
        self.message = Some(match play::play(self.cfg, &channel) {
            Ok(()) => (format!("Playing {} in mpv", channel.name), OK),
            Err(e) => (format!("{e:#}"), BAD),
        });
    }

    // ── keys ─────────────────────────────────────────────────────────────

    fn on_key(&mut self, key: KeyEvent) {
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.quit = true;
            return;
        }
        let confirmed = self.confirm_play.take();
        self.message = None;

        if self.typing {
            match key.code {
                KeyCode::Esc | KeyCode::Enter => self.typing = false,
                KeyCode::Up => self.move_selection(-1),
                KeyCode::Down => self.move_selection(1),
                KeyCode::Backspace => {
                    self.query.pop();
                    self.update_channels();
                }
                KeyCode::Char(c) => {
                    self.query.push(c);
                    self.update_channels();
                }
                _ => {}
            }
            return;
        }

        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Char('/') => {
                self.tab = Tab::Watch;
                self.typing = true;
            }
            KeyCode::Char('1') => self.tab = Tab::Watch,
            KeyCode::Char('2') => self.tab = Tab::Record,
            KeyCode::Char('3') => self.tab = Tab::Status,
            KeyCode::Tab => self.tab = TABS[(self.tab_index() + 1) % TABS.len()].0,
            KeyCode::BackTab => self.tab = TABS[(self.tab_index() + TABS.len() - 1) % TABS.len()].0,
            KeyCode::Char('r') => {
                self.refresh();
                self.message = Some(("Refreshed".into(), DIM));
            }
            _ if self.tab != Tab::Watch => {}
            KeyCode::Esc if !self.query.is_empty() => {
                self.query.clear();
                self.update_channels();
            }
            KeyCode::Up | KeyCode::Char('k') => self.move_selection(-1),
            KeyCode::Down | KeyCode::Char('j') => self.move_selection(1),
            KeyCode::PageUp => self.move_selection(-10),
            KeyCode::PageDown => self.move_selection(10),
            KeyCode::Home | KeyCode::Char('g') => self.move_selection(isize::MIN / 2),
            KeyCode::End | KeyCode::Char('G') => self.move_selection(isize::MAX / 2),
            KeyCode::Enter => self.play_selected(confirmed),
            _ => {}
        }
    }

    fn tab_index(&self) -> usize {
        TABS.iter().position(|(t, _)| *t == self.tab).unwrap_or(0)
    }

    // ── drawing ──────────────────────────────────────────────────────────

    fn draw(&mut self, f: &mut Frame) {
        let [header, body, footer] =
            Layout::vertical([Constraint::Length(2), Constraint::Min(0), Constraint::Length(1)]).areas(f.area());
        let body = body.inner(ratatui::layout::Margin { horizontal: 1, vertical: 0 });
        self.draw_header(f, header);
        match self.tab {
            Tab::Watch => self.draw_watch(f, body),
            Tab::Record => self.draw_record(f, body),
            Tab::Status => self.draw_status(f, body),
        }
        self.draw_footer(f, footer);
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

    fn draw_watch(&mut self, f: &mut Frame, area: Rect) {
        let [left, right] =
            Layout::horizontal([Constraint::Percentage(50), Constraint::Percentage(50)]).spacing(3).areas(area);
        let [search, _, label, list] =
            Layout::vertical([Constraint::Length(1), Constraint::Length(1), Constraint::Length(1), Constraint::Min(0)])
                .areas(left);

        let search_line = if self.typing {
            Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT).bold()), Span::raw(&self.query), Span::styled("▏", Style::new().fg(ACCENT))])
        } else if self.query.is_empty() {
            Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT)), Span::styled("search channels", Style::new().fg(DIM))])
        } else {
            Line::from(vec![Span::styled("/ ", Style::new().fg(DIM)), Span::raw(&self.query)])
        };
        f.render_widget(search_line, search);

        let heading = if self.query.trim().is_empty() {
            "FAVORITES".to_string()
        } else {
            format!("{} RESULTS", self.channels.len())
        };
        f.render_widget(Line::from(heading).fg(DIM).add_modifier(Modifier::BOLD), label);

        if self.channels.is_empty() {
            let text = if self.query.trim().is_empty() {
                "No favorites yet. Press / to search.".to_string()
            } else {
                format!("No channel matches “{}”.", self.query.trim())
            };
            f.render_widget(Paragraph::new(text).fg(DIM), list);
        } else {
            let items: Vec<ListItem> = self
                .channels
                .iter()
                .map(|c| {
                    ListItem::new(Line::from(vec![
                        Span::raw(c.name.clone()),
                        Span::styled(format!("  {}", c.category.as_deref().unwrap_or("")), Style::new().fg(DIM)),
                    ]))
                })
                .collect();
            let widget = List::new(items)
                .highlight_style(Style::new().fg(ACCENT).bold())
                .highlight_symbol("▌ ")
                .highlight_spacing(HighlightSpacing::Always);
            f.render_stateful_widget(widget, list, &mut self.list);
        }

        f.render_widget(Paragraph::new(self.detail_lines()).wrap(Wrap { trim: true }), right);
    }

    fn detail_lines(&self) -> Vec<Line<'static>> {
        let Some(channel) = self.selected() else { return vec![] };
        let mut lines = vec![
            Line::from(channel.name.clone()).bold(),
            Line::from(channel.category.clone().unwrap_or_default()).fg(DIM),
            Line::raw(""),
        ];
        if self.guide.is_empty() {
            lines.push(Line::from("No guide information.").fg(DIM));
        }
        let now = chrono::Utc::now().timestamp();
        for (i, p) in self.guide.iter().enumerate() {
            let on_now = p.start_ts <= now;
            let time = format!("{}–{}  ", hhmm(p.start_ts), hhmm(p.end_ts));
            let title = p.title.clone().unwrap_or_else(|| "(no title)".into());
            let line = if on_now {
                Line::from(vec![Span::styled(time, Style::new().fg(ACCENT)), Span::styled(title, Style::new().fg(ACCENT).bold())])
            } else {
                Line::from(vec![Span::styled(time, Style::new().fg(DIM)), Span::raw(title)])
            };
            lines.push(line);
            if i == 0 && on_now && !p.description.is_empty() {
                lines.push(Line::from(p.description.clone()).fg(DIM));
                lines.push(Line::raw(""));
            }
        }
        lines.push(Line::raw(""));
        lines.push(Line::from(format!("id {}", channel.stream_id)).fg(DIM));
        lines
    }

    fn draw_record(&self, f: &mut Frame, area: Rect) {
        let mut lines = vec![section("SCHEDULED")];
        if self.schedules.is_empty() {
            lines.push(Line::from("Nothing scheduled.").fg(DIM));
        }
        for s in &self.schedules {
            let (dot, color) = if s.enabled { ("● ", OK) } else { ("○ ", DIM) };
            lines.push(Line::from(vec![Span::styled(dot, Style::new().fg(color)), Span::raw(s.what.clone())]));
            let next = s.next_run.clone().unwrap_or_else(|| "not planned".into());
            lines.push(Line::from(format!("  next {next}")).fg(DIM));
        }

        lines.push(Line::raw(""));
        lines.push(section("RECENT RECORDINGS"));
        if self.runs.is_empty() {
            lines.push(Line::from("No recordings yet.").fg(DIM));
        }
        for r in &self.runs {
            let (mark, color) = match r.status.as_str() {
                "ok" => ("✓ ", OK),
                "warning" => ("! ", WARN),
                "failed" => ("✗ ", BAD),
                "running" => ("● ", ACCENT),
                _ => ("? ", DIM),
            };
            let name = r.show_name.clone().or_else(|| r.channel_name.clone()).unwrap_or_else(|| "?".into());
            lines.push(Line::from(vec![
                Span::styled(mark, Style::new().fg(color).bold()),
                Span::styled(format!("{:<18}", started(r.start_time.as_deref())), Style::new().fg(DIM)),
                Span::raw(name),
                Span::styled(format!("   {} / {}", r.recorded, r.planned), Style::new().fg(DIM)),
            ]));
            if let Some(problem) = r.problems.first() {
                lines.push(Line::from(format!("  {problem}")).fg(DIM));
            }
        }
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), area);
    }

    fn draw_status(&self, f: &mut Frame, area: Rect) {
        let s = &self.status;
        let mut lines = vec![section("SERVICES")];
        for svc in &s.services {
            let color = match svc.state.as_str() {
                "up" => OK,
                "stopped" => BAD,
                _ => WARN,
            };
            lines.push(Line::from(vec![
                Span::styled("● ", Style::new().fg(color)),
                Span::raw(format!("{:<18}", svc.name)),
                Span::styled(svc.detail.clone(), Style::new().fg(DIM)),
            ]));
        }

        lines.push(Line::raw(""));
        lines.push(section("NOW"));
        if s.recording.is_empty() {
            lines.push(row("Recording", "nothing".into(), DIM));
        }
        for r in &s.recording {
            lines.push(row("Recording", r.what.clone(), BAD));
        }
        let restream = match (&s.restream.live, &s.restream.channel_name) {
            (true, name) => (format!("{} since {}", name.as_deref().unwrap_or("?"), s.restream.since.as_deref().unwrap_or("?")), OK),
            (false, _) => ("off".into(), DIM),
        };
        lines.push(row("Restream", restream.0, restream.1));

        lines.push(Line::raw(""));
        lines.push(section("DATA"));
        lines.push(row("Channels", age(s.list_age_minutes), freshness(s.list_age_minutes, 3 * 60)));
        lines.push(row("Guide", age(s.guide_age_minutes), freshness(s.guide_age_minutes, 24 * 60)));
        if let Some(c) = &s.database {
            let counts = format!("{} live · {} movies · {} series · {} guide entries", c.live, c.vod, c.series, c.guide_entries);
            lines.push(row("Database", counts, Color::Reset));
        }
        if let Some(a) = &s.account {
            let text = format!("{} · expires {} · {} connection(s)", a.status, a.expires.as_deref().unwrap_or("?"), a.max_connections);
            lines.push(row("Account", text, Color::Reset));
        }
        f.render_widget(Paragraph::new(lines), area);
    }

    fn draw_footer(&self, f: &mut Frame, area: Rect) {
        let mut spans = vec![Span::raw(" ")];
        for r in &self.status.recording {
            spans.push(Span::styled(format!("● REC {}  ", short(&r.what)), Style::new().fg(BAD).bold()));
        }
        if self.status.restream.live {
            let name = self.status.restream.channel_name.as_deref().unwrap_or("?");
            spans.push(Span::styled(format!("● LIVE {name}  "), Style::new().fg(OK).bold()));
        }
        if let Some((text, color)) = &self.message {
            spans.push(Span::styled(text.clone(), Style::new().fg(*color)));
        }

        let hints = if self.typing {
            "esc/↵ done  ↑↓ move "
        } else if self.tab == Tab::Watch {
            "/ search  ↵ play  1-3 tabs  r refresh  q quit "
        } else {
            "/ search  1-3 tabs  r refresh  q quit "
        };
        let [left, right] =
            Layout::horizontal([Constraint::Min(0), Constraint::Length(hints.chars().count() as u16)]).areas(area);
        f.render_widget(Line::from(spans), left);
        f.render_widget(Line::from(hints).fg(DIM), right);
    }
}

fn section(title: &str) -> Line<'static> {
    Line::from(title.to_string()).fg(DIM).add_modifier(Modifier::BOLD)
}

fn row(label: &str, value: String, color: Color) -> Line<'static> {
    Line::from(vec![Span::styled(format!("{label:<11}"), Style::new().fg(DIM)), Span::styled(value, Style::new().fg(color))])
}

fn age(minutes: Option<i64>) -> String {
    match minutes {
        None => "never downloaded".into(),
        Some(m) if m < 60 => format!("updated {m} min ago"),
        Some(m) => format!("updated {}h{:02} ago", m / 60, m % 60),
    }
}

fn freshness(minutes: Option<i64>, stale_after: i64) -> Color {
    match minutes {
        Some(m) if m <= stale_after => Color::Reset,
        _ => WARN,
    }
}

fn hhmm(ts: i64) -> String {
    use chrono::TimeZone;
    chrono::Local.timestamp_opt(ts, 0).single().map(|t| t.format("%H:%M").to_string()).unwrap_or_default()
}

/// "2026-10-15T18:29:57" → "Thu 15 Oct 18:29"
fn started(text: Option<&str>) -> String {
    text.and_then(|t| chrono::NaiveDateTime::parse_from_str(t, "%Y-%m-%dT%H:%M:%S").ok())
        .map(|t| t.format("%a %d %b %H:%M").to_string())
        .unwrap_or_else(|| text.unwrap_or("?").to_string())
}

/// The channel part of a schedule description, for the footer.
fn short(what: &str) -> String {
    what.split(',').next().unwrap_or(what).to_string()
}
