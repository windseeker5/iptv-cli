//! Live, Movies and Series: the list in the middle, the selection on the right.
//!
//! With an empty search, Live shows your favorites and Movies/Series their
//! categories; ↵ opens, ← or esc goes back, `/` searches the current section.

use super::modal::{self, Action};
use super::theme::*;
use super::{hhmm, move_in, selected_style};
use crate::config::Config;
use crate::db::{Category, Channel, Db, Episode, Movie, Program, Series};
use crate::schedules::Schedule;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{HighlightSpacing, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

const LIMIT: usize = 500;
/// Rows that get their "on now" looked up (one quick query each).
const NOW_FOR: usize = 300;

#[derive(Default, Clone, Copy, PartialEq)]
pub enum Mode {
    #[default]
    Live,
    Movies,
    Series,
}

impl Mode {
    fn name(self) -> &'static str {
        match self {
            Mode::Live => "Live",
            Mode::Movies => "Movies",
            Mode::Series => "Series",
        }
    }
}

enum View {
    Channels(Vec<Channel>, Vec<Option<Program>>),
    Categories(Vec<Category>),
    Movies(Vec<Movie>),
    Series(Vec<Series>),
    Episodes { series: Series, episodes: Vec<Episode>, loading: bool },
}

impl View {
    fn len(&self) -> usize {
        match self {
            View::Channels(v, _) => v.len(),
            View::Categories(v) => v.len(),
            View::Movies(v) => v.len(),
            View::Series(v) => v.len(),
            View::Episodes { episodes, .. } => episodes.len(),
        }
    }
}

/// One level of browsing: what's listed, where the cursor is, and its name.
struct Level {
    view: View,
    list: ListState,
    label: String,
}

pub struct Watch {
    pub mode: Mode,
    pub query: String,
    pub typing: bool,
    level: Level,
    back: Vec<Level>,
    pub guide: Vec<Program>,
    /// Set when the user opened a series whose episodes still need downloading.
    pub wants_episodes: Option<i64>,
}

impl Default for Watch {
    fn default() -> Self {
        Watch {
            mode: Mode::Live,
            query: String::new(),
            typing: false,
            level: Level { view: View::Channels(vec![], vec![]), list: ListState::default(), label: String::new() },
            back: vec![],
            guide: vec![],
            wants_episodes: None,
        }
    }
}

fn now() -> i64 {
    chrono::Utc::now().timestamp()
}

fn on_now(db: &Db, channels: &[Channel]) -> Vec<Option<Program>> {
    let t = now();
    channels
        .iter()
        .enumerate()
        .map(|(i, c)| if i < NOW_FOR { db.guide(c.stream_id, t, 1).ok()?.into_iter().next().filter(|p| p.start_ts <= t) } else { None })
        .collect()
}

impl Watch {
    /// Rebuild the top list from the section and the search.
    pub fn reset(&mut self, db: &Db, cfg: &Config) {
        let q = self.query.trim();
        let searching = !q.is_empty();
        let (view, label) = match (self.mode, searching) {
            (Mode::Live, false) => {
                let channels = crate::favorites::live(&cfg.data_dir);
                let now = on_now(db, &channels);
                (View::Channels(channels, now), "Favorites".to_string())
            }
            (Mode::Live, true) => {
                let channels = db.search_channels(q, LIMIT).unwrap_or_default();
                let now = on_now(db, &channels);
                (View::Channels(channels, now), String::new())
            }
            (Mode::Movies, false) => (View::Categories(db.movie_categories().unwrap_or_default()), "Categories".into()),
            (Mode::Movies, true) => (View::Movies(db.search_movies(q, LIMIT).unwrap_or_default()), String::new()),
            (Mode::Series, false) => (View::Categories(db.series_categories().unwrap_or_default()), "Categories".into()),
            (Mode::Series, true) => (View::Series(db.search_series(q, LIMIT).unwrap_or_default()), String::new()),
        };
        let label = match (searching, view.len()) {
            (false, _) => label,
            (true, 1) => format!("1 result for “{q}”"),
            (true, n) => format!("{n} results for “{q}”"),
        };
        self.back.clear();
        self.level = Level { list: ListState::default().with_selected((view.len() > 0).then_some(0)), view, label };
        self.load_guide(db);
    }

    /// Recompute what's on now (the 30-second refresh).
    pub fn refresh_now(&mut self, db: &Db) {
        if let View::Channels(channels, now) = &mut self.level.view {
            *now = on_now(db, channels);
        }
        self.load_guide(db);
    }

    fn open(&mut self, view: View, label: String) {
        let list = ListState::default().with_selected((view.len() > 0).then_some(0));
        let previous = std::mem::replace(&mut self.level, Level { view, list, label });
        self.back.push(previous);
    }

    /// Go up one level, or clear the search. False when there is nothing to go back to.
    pub fn back(&mut self, db: &Db, cfg: &Config) -> bool {
        if let Some(previous) = self.back.pop() {
            self.level = previous;
        } else if !self.query.is_empty() {
            self.query.clear();
            self.reset(db, cfg);
        } else {
            return false;
        }
        self.load_guide(db);
        true
    }

    fn selected(&self) -> usize {
        self.level.list.selected().unwrap_or(0)
    }

    fn selected_channel(&self) -> Option<&Channel> {
        match &self.level.view {
            View::Channels(v, _) => v.get(self.selected()),
            _ => None,
        }
    }

    fn load_guide(&mut self, db: &Db) {
        self.guide = match self.selected_channel() {
            Some(c) => db.guide(c.stream_id, now(), 6).unwrap_or_default(),
            None => vec![],
        };
    }

    pub fn episodes_loaded(&mut self, series_id: i64, loaded: Vec<Episode>) {
        if let View::Episodes { series, episodes, loading } = &mut self.level.view
            && series.series_id == series_id
        {
            *episodes = loaded;
            *loading = false;
            self.level.list.select((!episodes.is_empty()).then_some(0));
        }
    }

    pub fn episodes_failed(&mut self) {
        if let View::Episodes { loading, .. } = &mut self.level.view {
            *loading = false;
        }
    }

    // ── keys ─────────────────────────────────────────────────────────────

    pub fn on_typing_key(&mut self, key: KeyEvent, db: &Db, cfg: &Config) {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => self.typing = false,
            KeyCode::Up | KeyCode::Down => {
                move_in(&mut self.level.list, key.code, self.level.view.len());
                self.load_guide(db);
            }
            KeyCode::Backspace => {
                self.query.pop();
                self.reset(db, cfg);
            }
            KeyCode::Char(c) => {
                self.query.push(c);
                self.reset(db, cfg);
            }
            _ => {}
        }
    }

    pub fn on_key(&mut self, key: KeyEvent, db: &Db) -> Option<Action> {
        let i = self.selected();
        match key.code {
            KeyCode::Enter | KeyCode::Right | KeyCode::Char('l') => self.enter(db, key.code == KeyCode::Enter),
            KeyCode::Char('r') => self.selected_channel().map(|c| Action::RecordMenu(c.clone())),
            KeyCode::Char('s') => self.selected_channel().map(|c| Action::RestreamStart { channel: c.clone(), replace: false }),
            KeyCode::Char('n') => {
                let channel = self.selected_channel()?.clone();
                let next = self.guide.iter().find(|p| p.start_ts > now())?;
                Some(modal::schedule_action(&channel, next))
            }
            KeyCode::Char('d') => match &self.level.view {
                View::Movies(v) => v.get(i).map(|m| Action::DownloadMovie(m.clone())),
                View::Episodes { series, episodes, .. } => episodes
                    .get(i)
                    .map(|e| Action::DownloadEpisodes { series: series.name.clone(), episodes: vec![e.clone()] }),
                _ => None,
            },
            KeyCode::Char('D') => match &self.level.view {
                View::Episodes { series, episodes, .. } if !episodes.is_empty() => {
                    Some(Action::DownloadEpisodes { series: series.name.clone(), episodes: episodes.clone() })
                }
                _ => None,
            },
            code => {
                if move_in(&mut self.level.list, code, self.level.view.len()) {
                    self.load_guide(db);
                }
                None
            }
        }
    }

    /// ↵ plays or opens; → only opens (categories, series).
    fn enter(&mut self, db: &Db, play: bool) -> Option<Action> {
        let i = self.selected();
        match &self.level.view {
            View::Channels(v, _) if play => v.get(i).map(|c| Action::PlayLive(c.clone())),
            View::Movies(v) if play => v.get(i).map(|m| Action::PlayMovie(m.clone())),
            View::Episodes { series, episodes, .. } if play => {
                episodes.get(i).map(|e| Action::PlayEpisode { series: series.name.clone(), episode: e.clone() })
            }
            View::Categories(v) => {
                let category = v.get(i)?.clone();
                let view = match (self.mode, category.id) {
                    (Mode::Movies, Some(id)) => View::Movies(db.movies_in(id, LIMIT).unwrap_or_default()),
                    _ => View::Series(db.series_in(&category.name, LIMIT).unwrap_or_default()),
                };
                self.open(view, category.name);
                None
            }
            View::Series(v) => {
                let series = v.get(i)?.clone();
                let episodes = db.episodes(series.series_id).unwrap_or_default();
                let loading = episodes.is_empty();
                if loading {
                    self.wants_episodes = Some(series.series_id);
                }
                let label = series.name.clone();
                self.open(View::Episodes { series, episodes, loading }, label);
                None
            }
            _ => None,
        }
    }

    pub fn hints(&self) -> &'static str {
        if self.typing {
            return "type to search  ↵ done  ↑↓ move";
        }
        match &self.level.view {
            View::Channels(..) => "↵ watch  r record  s restream  n next  / search",
            View::Categories(_) => "↵ open  / search  ← back",
            View::Movies(_) => "↵ watch  d download  / search  ← back",
            View::Series(_) => "↵ episodes  / search  ← back",
            View::Episodes { .. } => "↵ watch  d download  D all  ← back",
        }
    }

    // ── the list panel ───────────────────────────────────────────────────

    pub fn draw_list(&mut self, f: &mut Frame, area: Rect, focused: bool) {
        let title = match &self.level.view {
            View::Episodes { loading: true, .. } => format!("{} · {} · loading…", self.mode.name(), self.level.label),
            _ => format!("{} · {}", self.mode.name(), self.level.label),
        };
        let block = panel(&title, focused).title_top(Line::from(" / search ").fg(DIM).right_aligned());
        let inner = block.inner(area);
        f.render_widget(block, area);

        let searching = self.typing || !self.query.is_empty();
        let [search, header, list] = Layout::vertical([
            Constraint::Length(if searching { 2 } else { 0 }),
            Constraint::Length(1),
            Constraint::Min(0),
        ])
        .areas(inner);
        if searching {
            let cursor = if self.typing { Span::styled("▏", Style::new().fg(ACCENT)) } else { Span::raw("") };
            f.render_widget(Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT).bold()), Span::raw(self.query.clone()), cursor]), search);
        }

        let w = inner.width as usize;
        let (head, items) = self.rows(w);
        f.render_widget(Line::from(head).fg(DIM).bold(), header);
        if items.is_empty() {
            f.render_widget(Paragraph::new(self.empty_text()).fg(DIM).wrap(Wrap { trim: true }), list);
            return;
        }
        let widget = List::new(items).highlight_style(selected_style()).highlight_spacing(HighlightSpacing::Never);
        f.render_stateful_widget(widget, list, &mut self.level.list);
    }

    /// The column header and one row per item, fitted to `w` columns.
    fn rows(&self, w: usize) -> (String, Vec<ListItem<'static>>) {
        let t = now();
        let dim_style = Style::new().fg(DIM);
        match &self.level.view {
            View::Channels(channels, now_on) => {
                let (name_w, left_w) = ((w * 2 / 5).clamp(12, 28), 12);
                let show_w = w.saturating_sub(name_w + left_w + 2);
                let head = format!("{}  {}{:>left_w$}", fit("CHANNEL", name_w), fit("ON NOW", show_w), "LEFT");
                let items = channels
                    .iter()
                    .zip(now_on)
                    .map(|(c, p)| {
                        let (show, left_text) = match p {
                            Some(p) => {
                                let done = (t - p.start_ts) as f64 / (p.end_ts - p.start_ts).max(1) as f64;
                                (p.title.clone().unwrap_or_default(), format!("{} {:>6}", blocks(done, 5), left(p.end_ts - t)))
                            }
                            None => (String::new(), String::new()),
                        };
                        ListItem::new(Line::from(vec![
                            Span::raw(format!("{}  ", fit(display_name(&c.name), name_w))),
                            Span::styled(fit(&show, show_w), dim_style),
                            Span::styled(format!("{left_text:>left_w$}"), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
            View::Categories(v) => ("CATEGORY".into(), v.iter().map(|c| ListItem::new(fit(&c.name, w))).collect()),
            View::Movies(v) => {
                let name_w = w.saturating_sub(14);
                let head = format!("{}{:>6}{:>8}", fit("TITLE", name_w), "YEAR", "RATING");
                let items = v
                    .iter()
                    .map(|m| {
                        let rating = m.rating.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}")).unwrap_or_default();
                        ListItem::new(Line::from(vec![
                            Span::raw(fit(&m.name, name_w)),
                            Span::styled(format!("{:>6}{rating:>8}", m.year.clone().unwrap_or_default()), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
            View::Series(v) => {
                let genre_w = 18.min(w / 3);
                let name_w = w.saturating_sub(genre_w + 2);
                let head = format!("{}  {}", fit("SERIES", name_w), fit("GENRE", genre_w));
                let items = v
                    .iter()
                    .map(|s| {
                        ListItem::new(Line::from(vec![
                            Span::raw(format!("{}  ", fit(&s.name, name_w))),
                            Span::styled(fit(s.genre.as_deref().unwrap_or(""), genre_w), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
            View::Episodes { episodes, .. } => {
                let title_w = w.saturating_sub(8 + 12);
                let head = format!("{:<8}{}{:>12}", "EP", fit("TITLE", title_w), "AIRED");
                let items = episodes
                    .iter()
                    .map(|e| {
                        ListItem::new(Line::from(vec![
                            Span::styled(format!("{:<8}", format!("S{:02}E{:02}", e.season, e.episode_num)), Style::new().fg(ACCENT)),
                            Span::raw(fit(episode_title(e), title_w)),
                            Span::styled(format!("{:>12}", e.air_date.clone().unwrap_or_default()), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
        }
    }

    fn empty_text(&self) -> String {
        match (&self.level.view, self.query.trim()) {
            (View::Episodes { loading: true, .. }, _) => "Asking the provider for the episodes…".into(),
            (View::Episodes { .. }, _) => "No episodes.".into(),
            (View::Channels(..), "") => "No favorites yet. Press / to search for a channel.".into(),
            (_, "") => "Nothing here.".into(),
            (_, q) => format!("Nothing matches “{q}”."),
        }
    }

    // ── the detail panel ─────────────────────────────────────────────────

    pub fn draw_detail(&self, f: &mut Frame, area: Rect, schedules: &[Schedule]) {
        let block = panel("", false);
        let inner = block.inner(area);
        f.render_widget(block, area);
        let i = self.selected();
        let w = inner.width as usize;
        let meta = |parts: Vec<Option<String>>| parts.into_iter().flatten().collect::<Vec<_>>().join("  ·  ");
        let rating = |r: Option<f64>| r.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}"));

        let (lines, actions): (Vec<Line>, Vec<(&str, &str)>) = match &self.level.view {
            View::Channels(v, _) => match v.get(i) {
                Some(c) => (self.channel_lines(c, w, schedules), vec![("↵", "watch"), ("r", "record"), ("s", "restream"), ("n", "schedule next")]),
                None => (vec![], vec![]),
            },
            View::Categories(v) => match v.get(i) {
                Some(c) => (vec![Line::from(c.name.clone()).bold()], vec![("↵", "open")]),
                None => (vec![], vec![]),
            },
            View::Movies(v) => match v.get(i) {
                Some(m) => (
                    vec![
                        Line::from(dim("MOVIE")),
                        Line::from(m.name.clone()).bold(),
                        Line::from(dim(meta(vec![m.year.clone(), rating(m.rating), m.genre.clone()]))),
                    ],
                    vec![("↵", "watch"), ("d", "download")],
                ),
                None => (vec![], vec![]),
            },
            View::Series(v) => match v.get(i) {
                Some(s) => {
                    let mut lines = vec![
                        Line::from(dim(s.category.clone().unwrap_or_else(|| "SERIES".into()))),
                        Line::from(s.name.clone()).bold(),
                        Line::from(dim(meta(vec![s.genre.clone(), rating(s.rating)]))),
                        Line::raw(""),
                    ];
                    if let Some(plot) = &s.plot {
                        lines.push(Line::raw(plot.clone()));
                        lines.push(Line::raw(""));
                    }
                    if let Some(cast) = &s.cast {
                        lines.push(Line::from(dim(format!("With {cast}"))));
                    }
                    (lines, vec![("↵", "episodes")])
                }
                None => (vec![], vec![]),
            },
            View::Episodes { series, episodes, .. } => match episodes.get(i) {
                Some(e) => (
                    vec![
                        Line::from(dim(series.name.clone())),
                        Line::from(format!("S{:02}E{:02}", e.season, e.episode_num)).fg(ACCENT).bold(),
                        Line::from(episode_title(e).to_string()).bold(),
                        Line::from(dim(meta(vec![e.air_date.clone(), e.duration.clone()]))),
                        Line::raw(""),
                        Line::from(dim(format!("{} episodes in this series", episodes.len()))),
                    ],
                    vec![("↵", "watch"), ("d", "download"), ("D", "all episodes")],
                ),
                None => (vec![Line::from(series.name.clone()).bold()], vec![]),
            },
        };
        let used = chips(f, inner, &actions);
        let text = Rect { height: inner.height.saturating_sub(used), ..inner };
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), text);
    }

    fn channel_lines(&self, channel: &Channel, w: usize, schedules: &[Schedule]) -> Vec<Line<'static>> {
        let t = now();
        let mut lines = vec![Line::from(dim(channel.name.clone()))];
        let current = self.guide.first().filter(|p| p.start_ts <= t);
        match current {
            Some(p) => {
                lines.push(Line::from(p.title.clone().unwrap_or_else(|| "(no title)".into())).bold());
                lines.push(Line::from(dim(format!("{} – {}", hhmm(p.start_ts), hhmm(p.end_ts)))));
                let done = (t - p.start_ts) as f64 / (p.end_ts - p.start_ts).max(1) as f64;
                let tail = format!(" {} left", left(p.end_ts - t));
                let mut gauge_line = gauge(done, w.saturating_sub(tail.len()));
                gauge_line.push(dim(tail));
                lines.push(Line::from(gauge_line));
                if !p.description.is_empty() {
                    lines.push(Line::raw(""));
                    lines.push(Line::raw(p.description.clone()));
                }
            }
            None => lines.push(Line::from("No guide information").bold()),
        }
        let upcoming: Vec<&Program> = self.guide.iter().filter(|p| p.start_ts > t).take(4).collect();
        if !upcoming.is_empty() {
            lines.push(Line::raw(""));
            lines.push(Line::from(Span::styled("─".repeat(w), Style::new().fg(BORDER))));
            lines.push(heading("NEXT"));
        }
        for p in upcoming {
            let title = p.title.clone().unwrap_or_else(|| "(no title)".into());
            let rec = if will_record(schedules, channel.stream_id, p.start_ts) { "● rec" } else { "" };
            let title_w = w.saturating_sub(7 + rec.len() + 1);
            lines.push(Line::from(vec![
                dim(format!("{}  ", hhmm(p.start_ts))),
                Span::raw(fit(&title, title_w)),
                Span::styled(format!(" {rec}"), Style::new().fg(BAD)),
            ]));
        }
        lines
    }
}

/// "EN - Mr Bates vs The Post Office - S01E01 - Episode 1" → "Episode 1"
fn episode_title(e: &Episode) -> &str {
    let code = format!(" - S{:02}E{:02} - ", e.season, e.episode_num);
    match e.title.split_once(code.as_str()) {
        Some((_, rest)) if !rest.trim().is_empty() => rest,
        _ => &e.title,
    }
}

/// "CA FR: NOOVO V-TELE HD" → "NOOVO V-TELE HD": the country tag only adds noise in a list.
fn display_name(name: &str) -> &str {
    match name.split_once(": ") {
        Some((tag, rest)) if tag.len() <= 6 && !rest.is_empty() => rest,
        _ => name,
    }
}

/// Whether an enabled schedule records this channel at this start time.
pub fn will_record(schedules: &[Schedule], stream_id: i64, start_ts: i64) -> bool {
    let Some(start) = chrono::DateTime::from_timestamp(start_ts, 0).map(|t| t.with_timezone(&chrono::Local)) else { return false };
    let (time, day, date) = (start.format("%H:%M").to_string(), start.format("%a").to_string(), start.format("%Y-%m-%d").to_string());
    schedules.iter().any(|s| {
        s.enabled && s.stream_id == stream_id && s.time == time && (s.days.contains(&day) || s.date.as_deref() == Some(date.as_str()))
    })
}
