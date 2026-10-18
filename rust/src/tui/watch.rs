//! The Watch tab: live channels, movies and series, search first.
//!
//! ←/→ switch between Live, Movies and Series. With an empty search, Live shows
//! your favorites and Movies/Series show their categories; ↵ opens, esc goes back.

use super::modal::Action;
use super::{hhmm, move_in, ACCENT, DIM};
use crate::config::Config;
use crate::db::{Category, Channel, Db, Episode, Movie, Program, Series};
use crate::status::Status;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{HighlightSpacing, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

const LIMIT: usize = 500;

#[derive(Default, Clone, Copy, PartialEq)]
pub enum Mode {
    #[default]
    Live,
    Movies,
    Series,
}

const MODES: [(Mode, &str); 3] = [(Mode::Live, "Live"), (Mode::Movies, "Movies"), (Mode::Series, "Series")];

enum View {
    Channels(Vec<Channel>),
    Categories(Vec<Category>),
    Movies(Vec<Movie>),
    Series(Vec<Series>),
    Episodes { series: Series, episodes: Vec<Episode>, loading: bool },
}

impl View {
    fn len(&self) -> usize {
        match self {
            View::Channels(v) => v.len(),
            View::Categories(v) => v.len(),
            View::Movies(v) => v.len(),
            View::Series(v) => v.len(),
            View::Episodes { episodes, .. } => episodes.len(),
        }
    }
}

/// One level of browsing: what's listed, where the cursor is, and its heading.
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
            level: Level { view: View::Channels(vec![]), list: ListState::default(), label: String::new() },
            back: vec![],
            guide: vec![],
            wants_episodes: None,
        }
    }
}

impl Watch {
    /// Rebuild the top list from the mode and the search.
    pub fn reset(&mut self, db: &Db, cfg: &Config) {
        let q = self.query.trim();
        let searching = !q.is_empty();
        let (view, label) = match (self.mode, searching) {
            (Mode::Live, false) => (View::Channels(crate::favorites::live(&cfg.data_dir)), "FAVORITES".to_string()),
            (Mode::Live, true) => (View::Channels(db.search_channels(q, LIMIT).unwrap_or_default()), String::new()),
            (Mode::Movies, false) => (View::Categories(db.movie_categories().unwrap_or_default()), "MOVIE CATEGORIES".into()),
            (Mode::Movies, true) => (View::Movies(db.search_movies(q, LIMIT).unwrap_or_default()), String::new()),
            (Mode::Series, false) => (View::Categories(db.series_categories().unwrap_or_default()), "SERIES CATEGORIES".into()),
            (Mode::Series, true) => (View::Series(db.search_series(q, LIMIT).unwrap_or_default()), String::new()),
        };
        let label = match (searching, view.len()) {
            (false, _) => label,
            (true, 1) => "1 RESULT".into(),
            (true, n) => format!("{n} RESULTS"),
        };
        self.back.clear();
        self.level = Level { list: ListState::default().with_selected((view.len() > 0).then_some(0)), view, label };
        self.load_guide(db);
    }

    fn open(&mut self, view: View, label: String) {
        let list = ListState::default().with_selected((view.len() > 0).then_some(0));
        let previous = std::mem::replace(&mut self.level, Level { view, list, label });
        self.back.push(previous);
    }

    fn selected(&self) -> usize {
        self.level.list.selected().unwrap_or(0)
    }

    fn selected_channel(&self) -> Option<&Channel> {
        match &self.level.view {
            View::Channels(v) => v.get(self.selected()),
            _ => None,
        }
    }

    pub fn load_guide(&mut self, db: &Db) {
        let now = chrono::Utc::now().timestamp();
        self.guide = match self.selected_channel() {
            Some(c) => db.guide(c.stream_id, now, 5).unwrap_or_default(),
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

    pub fn on_key(&mut self, key: KeyEvent, db: &Db, cfg: &Config) -> Option<Action> {
        let i = self.selected();
        match key.code {
            KeyCode::Left | KeyCode::Right | KeyCode::Char('h') | KeyCode::Char('l') => {
                let at = MODES.iter().position(|(m, _)| *m == self.mode).unwrap_or(0);
                let step = if matches!(key.code, KeyCode::Left | KeyCode::Char('h')) { MODES.len() - 1 } else { 1 };
                self.mode = MODES[(at + step) % MODES.len()].0;
                self.reset(db, cfg);
                None
            }
            KeyCode::Esc | KeyCode::Backspace => {
                if let Some(previous) = self.back.pop() {
                    self.level = previous;
                } else if !self.query.is_empty() {
                    self.query.clear();
                    self.reset(db, cfg);
                }
                self.load_guide(db);
                None
            }
            KeyCode::Enter => self.enter(db),
            KeyCode::Char('r') => self.selected_channel().map(|c| Action::RecordMenu(c.clone())),
            KeyCode::Char('s') => self.selected_channel().map(|c| Action::RestreamStart { channel: c.clone(), replace: false }),
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

    fn enter(&mut self, db: &Db) -> Option<Action> {
        let i = self.selected();
        match &self.level.view {
            View::Channels(v) => v.get(i).map(|c| Action::PlayLive(c.clone())),
            View::Movies(v) => v.get(i).map(|m| Action::PlayMovie(m.clone())),
            View::Episodes { series, episodes, .. } => {
                episodes.get(i).map(|e| Action::PlayEpisode { series: series.name.clone(), episode: e.clone() })
            }
            View::Categories(v) => {
                let category = v.get(i)?.clone();
                let view = match (self.mode, category.id) {
                    (Mode::Movies, Some(id)) => View::Movies(db.movies_in(id, LIMIT).unwrap_or_default()),
                    _ => View::Series(db.series_in(&category.name, LIMIT).unwrap_or_default()),
                };
                self.open(view, category.name.to_uppercase());
                None
            }
            View::Series(v) => {
                let series = v.get(i)?.clone();
                let episodes = db.episodes(series.series_id).unwrap_or_default();
                let loading = episodes.is_empty();
                if loading {
                    self.wants_episodes = Some(series.series_id);
                }
                let label = series.name.to_uppercase();
                self.open(View::Episodes { series, episodes, loading }, label);
                None
            }
        }
    }

    pub fn hints(&self) -> &'static str {
        if self.typing {
            return "esc/↵ done  ↑↓ move";
        }
        match &self.level.view {
            View::Channels(_) => "/ search  ↵ play  r record  s restream  ←→ movies/series",
            View::Categories(_) => "/ search  ↵ open  ←→ live/movies/series",
            View::Movies(_) => "/ search  ↵ play  d download  esc back",
            View::Series(_) => "/ search  ↵ episodes  esc back",
            View::Episodes { .. } => "↵ play  d download  D download all  esc back",
        }
    }

    // ── drawing ──────────────────────────────────────────────────────────

    pub fn draw(&mut self, f: &mut Frame, area: Rect, _status: &Status) {
        let [left, right] =
            Layout::horizontal([Constraint::Percentage(50), Constraint::Percentage(50)]).spacing(3).areas(area);
        let [modes, search, _, label, list] = Layout::vertical([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(0),
        ])
        .areas(left);

        let mut spans = vec![];
        for (mode, name) in MODES {
            let style = if mode == self.mode { Style::new().fg(ACCENT).bold() } else { Style::new().fg(DIM) };
            spans.push(Span::styled(name, style));
            spans.push(Span::raw("   "));
        }
        f.render_widget(Line::from(spans), modes);

        let search_line = if self.typing {
            Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT).bold()), Span::raw(self.query.clone()), Span::styled("▏", Style::new().fg(ACCENT))])
        } else if self.query.is_empty() {
            Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT)), Span::styled("search", Style::new().fg(DIM))])
        } else {
            Line::from(vec![Span::styled("/ ", Style::new().fg(DIM)), Span::raw(self.query.clone())])
        };
        f.render_widget(search_line, search);

        let heading = match &self.level.view {
            View::Episodes { loading: true, .. } => format!("{}  ·  loading episodes…", self.level.label),
            _ => self.level.label.clone(),
        };
        f.render_widget(Line::from(heading).fg(DIM).add_modifier(Modifier::BOLD), label);

        let items = self.items();
        if items.is_empty() {
            f.render_widget(Paragraph::new(self.empty_text()).fg(DIM), list);
        } else {
            let widget = List::new(items)
                .highlight_style(Style::new().fg(ACCENT).bold())
                .highlight_symbol("▌ ")
                .highlight_spacing(HighlightSpacing::Always);
            f.render_stateful_widget(widget, list, &mut self.level.list);
        }
        f.render_widget(Paragraph::new(self.detail_lines()).wrap(Wrap { trim: true }), right);
    }

    fn empty_text(&self) -> String {
        match (&self.level.view, self.query.trim()) {
            (View::Episodes { loading: true, .. }, _) => "Asking the provider for the episodes…".into(),
            (View::Episodes { .. }, _) => "No episodes.".into(),
            (View::Channels(_), "") => "No favorites yet. Press / to search.".into(),
            (_, "") => "Nothing here.".into(),
            (_, q) => format!("Nothing matches “{q}”."),
        }
    }

    fn items(&self) -> Vec<ListItem<'static>> {
        let pair = |a: String, b: String| ListItem::new(Line::from(vec![Span::raw(a), Span::styled(format!("  {b}"), Style::new().fg(DIM))]));
        match &self.level.view {
            View::Channels(v) => v.iter().map(|c| pair(c.name.clone(), c.category.clone().unwrap_or_default())).collect(),
            View::Categories(v) => v.iter().map(|c| ListItem::new(c.name.clone())).collect(),
            View::Movies(v) => v.iter().map(|m| pair(m.name.clone(), m.year.clone().unwrap_or_default())).collect(),
            View::Series(v) => v.iter().map(|s| pair(s.name.clone(), s.genre.clone().unwrap_or_default())).collect(),
            View::Episodes { episodes, .. } => episodes
                .iter()
                .map(|e| pair(format!("S{:02}E{:02}  {}", e.season, e.episode_num, e.title), e.air_date.clone().unwrap_or_default()))
                .collect(),
        }
    }

    fn detail_lines(&self) -> Vec<Line<'static>> {
        let i = self.selected();
        let dim = |s: String| Line::from(s).fg(DIM);
        let meta = |parts: Vec<Option<String>>| parts.into_iter().flatten().collect::<Vec<_>>().join("  ·  ");
        match &self.level.view {
            View::Channels(v) => match v.get(i) {
                Some(c) => self.channel_lines(c),
                None => vec![],
            },
            View::Categories(v) => match v.get(i) {
                Some(c) => vec![Line::from(c.name.clone()).bold(), Line::raw(""), dim("↵ open".into())],
                None => vec![],
            },
            View::Movies(v) => match v.get(i) {
                Some(m) => vec![
                    Line::from(m.name.clone()).bold(),
                    dim(meta(vec![m.year.clone(), m.rating.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}")), m.genre.clone()])),
                    Line::raw(""),
                    dim("↵ play in mpv   d download".into()),
                ],
                None => vec![],
            },
            View::Series(v) => match v.get(i) {
                Some(s) => {
                    let mut lines = vec![
                        Line::from(s.name.clone()).bold(),
                        dim(meta(vec![s.genre.clone(), s.rating.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}")), s.category.clone()])),
                        Line::raw(""),
                    ];
                    if let Some(plot) = &s.plot {
                        lines.push(Line::raw(plot.clone()));
                        lines.push(Line::raw(""));
                    }
                    if let Some(cast) = &s.cast {
                        lines.push(dim(format!("With {cast}")));
                        lines.push(Line::raw(""));
                    }
                    lines.push(dim("↵ episodes".into()));
                    lines
                }
                None => vec![],
            },
            View::Episodes { series, episodes, .. } => {
                let mut lines = vec![Line::from(series.name.clone()).bold(), dim(format!("{} episodes", episodes.len())), Line::raw("")];
                if let Some(e) = episodes.get(i) {
                    lines.push(Line::from(format!("S{:02}E{:02}  {}", e.season, e.episode_num, e.title)).fg(ACCENT));
                    lines.push(dim(meta(vec![e.air_date.clone(), e.duration.clone()])));
                    lines.push(Line::raw(""));
                    lines.push(dim("↵ play   d download   D download all".into()));
                }
                lines
            }
        }
    }

    fn channel_lines(&self, channel: &Channel) -> Vec<Line<'static>> {
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
            lines.push(if on_now {
                Line::from(vec![Span::styled(time, Style::new().fg(ACCENT)), Span::styled(title, Style::new().fg(ACCENT).bold())])
            } else {
                Line::from(vec![Span::styled(time, Style::new().fg(DIM)), Span::raw(title)])
            });
            if i == 0 && on_now && !p.description.is_empty() {
                lines.push(Line::from(p.description.clone()).fg(DIM));
                lines.push(Line::raw(""));
            }
        }
        lines.push(Line::raw(""));
        lines.push(Line::from(format!("↵ play   r record   s restream   ·   id {}", channel.stream_id)).fg(DIM));
        lines
    }
}
