//! My list and All: the list in the middle, the selection on the right.
//!
//! My list is what you saved (channels, movies and series together); All is the
//! provider's own lists: Live, Movies, Series, then their categories. `f` saves or
//! removes the selection, ↵ opens, ← or esc goes back, `/` searches the section.
//! A series opens on its first season; `[` and `]` change season.

use super::modal::{self, Action};
use super::theme::*;
use super::{hhmm, move_in, selected_style};
use crate::config::Config;
use crate::db::{event_info, Category, Channel, Db, Episode, Movie, Program, Series};
use crate::downloads::{self, Job, Source, State};
use crate::favorites::{self, Kind, Saved};
use crate::schedules::Schedule;
use crate::status::Status;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{HighlightSpacing, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;
use std::collections::{HashMap, HashSet};

const LIMIT: usize = 500;
/// Rows that get their "on now" looked up (one quick query each).
const NOW_FOR: usize = 300;
/// All's first screen.
const KINDS: [Kind; 3] = [Kind::Live, Kind::Movie, Kind::Series];
/// Marks a saved row in All.
const SAVED: &str = "♥ ";

#[derive(Default, Clone, Copy, PartialEq)]
pub enum Mode {
    #[default]
    Mine,
    All,
}

impl Mode {
    fn name(self) -> &'static str {
        match self {
            Mode::Mine => "My list",
            Mode::All => "All",
        }
    }
}

fn kind_name(kind: Kind) -> &'static str {
    match kind {
        Kind::Live => "Live",
        Kind::Movie => "Movies",
        Kind::Series => "Series",
    }
}

/// Something you can save: a channel, a movie or a series.
#[derive(Clone)]
enum Item {
    Channel(Channel),
    Movie(Movie),
    Series(Series),
}

impl Item {
    fn key(&self) -> (Kind, i64) {
        match self {
            Item::Channel(c) => (Kind::Live, c.stream_id),
            Item::Movie(m) => (Kind::Movie, m.stream_id),
            Item::Series(s) => (Kind::Series, s.series_id),
        }
    }

    fn saved(&self) -> Saved {
        let (kind, id) = self.key();
        let (name, category) = match self {
            Item::Channel(c) => (c.name.clone(), c.category.clone()),
            Item::Movie(m) => (m.name.clone(), None),
            Item::Series(s) => (s.name.clone(), s.category.clone()),
        };
        Saved { kind, id, name, category }
    }

    fn name(&self) -> &str {
        match self {
            Item::Channel(c) => &c.name,
            Item::Movie(m) => &m.name,
            Item::Series(s) => &s.name,
        }
    }

    fn channel(&self) -> Option<&Channel> {
        match self {
            Item::Channel(c) => Some(c),
            _ => None,
        }
    }
}

enum View {
    /// All's first screen: Live, Movies, Series.
    Kinds,
    Categories(Kind, Vec<Category>),
    Channels(Vec<Channel>, Vec<Option<Program>>),
    Movies(Vec<Movie>),
    Series(Vec<Series>),
    /// My list, and searches in All: channels, movies and series together.
    Mixed(Vec<Item>, Vec<Option<Program>>),
    Episodes { series: Series, all: Vec<Episode>, season: i64, loading: bool },
}

impl View {
    fn len(&self) -> usize {
        match self {
            View::Kinds => KINDS.len(),
            View::Categories(_, v) => v.len(),
            View::Channels(v, _) => v.len(),
            View::Movies(v) => v.len(),
            View::Series(v) => v.len(),
            View::Mixed(v, _) => v.len(),
            View::Episodes { all, season, .. } => all.iter().filter(|e| e.season == *season).count(),
        }
    }
}

/// Where an episode stands on this machine.
#[derive(Clone)]
enum EpisodeState {
    OnDisk,
    Downloading(Option<f64>),
    Queued,
    Waiting,
    /// The last try failed; Downloads says why.
    Failed,
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
    episode_state: HashMap<String, EpisodeState>,
    /// What My list holds, to mark it in All.
    saved: HashSet<(Kind, i64)>,
    /// Set when the user opened a series whose episodes still need downloading.
    pub wants_episodes: Option<i64>,
}

impl Default for Watch {
    fn default() -> Self {
        Watch {
            mode: Mode::Mine,
            query: String::new(),
            typing: false,
            level: Level { view: View::Mixed(vec![], vec![]), list: ListState::default(), label: String::new() },
            back: vec![],
            guide: vec![],
            episode_state: HashMap::new(),
            saved: HashSet::new(),
            wants_episodes: None,
        }
    }
}

fn now() -> i64 {
    chrono::Utc::now().timestamp()
}

fn on_now<'a>(db: &Db, channels: impl Iterator<Item = Option<&'a Channel>>) -> Vec<Option<Program>> {
    let t = now();
    channels
        .enumerate()
        .map(|(i, c)| if i < NOW_FOR { db.guide(c?.stream_id, t, 1).ok()?.into_iter().next().filter(|p| p.start_ts <= t) } else { None })
        .collect()
}

fn mixed(db: &Db, items: Vec<Item>) -> View {
    let now = on_now(db, items.iter().map(Item::channel));
    View::Mixed(items, now)
}

/// My list as it is in the file; a movie or series the provider dropped is left out.
fn my_items(db: &Db, cfg: &Config) -> Vec<Item> {
    favorites::list(&cfg.data_dir)
        .into_iter()
        .filter_map(|s| match s.kind {
            Kind::Live => Some(Item::Channel(Channel { stream_id: s.id, name: s.name, category: s.category })),
            Kind::Movie => db.movie(s.id).ok().flatten().map(Item::Movie),
            Kind::Series => db.series(s.id).ok().flatten().map(Item::Series),
        })
        .collect()
}

fn seasons(all: &[Episode]) -> Vec<i64> {
    let mut s: Vec<i64> = all.iter().map(|e| e.season).collect();
    s.sort();
    s.dedup();
    s
}

impl Watch {
    /// The top list for the section and the search, with its name.
    fn top(&self, db: &Db, cfg: &Config) -> (View, String) {
        let q = self.query.trim();
        let (view, label) = match (self.mode, q.is_empty()) {
            (Mode::Mine, true) => (mixed(db, my_items(db, cfg)), String::new()),
            (Mode::Mine, false) => {
                let words: Vec<String> = q.split_whitespace().map(str::to_lowercase).collect();
                let items = my_items(db, cfg).into_iter().filter(|i| words.iter().all(|w| i.name().to_lowercase().contains(w))).collect();
                (mixed(db, items), String::new())
            }
            (Mode::All, true) => (View::Kinds, "from your provider".into()),
            (Mode::All, false) => {
                let mut items: Vec<Item> = db.search_channels(q, LIMIT).unwrap_or_default().into_iter().map(Item::Channel).collect();
                items.extend(db.search_series(q, LIMIT).unwrap_or_default().into_iter().map(Item::Series));
                items.extend(db.search_movies(q, LIMIT).unwrap_or_default().into_iter().map(Item::Movie));
                (mixed(db, items), String::new())
            }
        };
        let label = match (q.is_empty(), view.len()) {
            (true, _) => label,
            (false, 1) => format!("1 result for “{q}”"),
            (false, n) => format!("{n} results for “{q}”"),
        };
        (view, label)
    }

    /// Rebuild the top list from the section and the search.
    pub fn reset(&mut self, db: &Db, cfg: &Config) {
        self.saved = favorites::list(&cfg.data_dir).iter().map(|s| (s.kind, s.id)).collect();
        let (view, label) = self.top(db, cfg);
        self.back.clear();
        self.level = Level { list: ListState::default().with_selected((view.len() > 0).then_some(0)), view, label };
        self.load_guide(db);
    }

    /// How many things My list holds.
    pub fn saved_count(&self) -> usize {
        self.saved.len()
    }

    /// After `f`: mark the change, and rebuild My list where the cursor was.
    pub fn saved_changed(&mut self, db: &Db, cfg: &Config) {
        self.saved = favorites::list(&cfg.data_dir).iter().map(|s| (s.kind, s.id)).collect();
        if self.mode == Mode::Mine {
            let (view, label) = self.top(db, cfg);
            let level = match self.back.first_mut() {
                Some(level) => level,
                None => &mut self.level,
            };
            let keep = level.list.selected().unwrap_or(0).min(view.len().saturating_sub(1));
            *level = Level { list: ListState::default().with_selected((view.len() > 0).then_some(keep)), view, label };
        }
        self.load_guide(db);
    }

    /// Show a search in All with one item selected (from the Search section).
    /// For a series, open its episodes right away.
    pub fn show(&mut self, kind: Kind, query: &str, id: i64, db: &Db, cfg: &Config) {
        self.mode = Mode::All;
        self.query = query.to_string();
        self.typing = false;
        self.reset(db, cfg);
        let index = match &self.level.view {
            View::Mixed(v, _) => v.iter().position(|i| i.key() == (kind, id)),
            _ => None,
        };
        self.level.list.select(index.or(Some(0)));
        self.load_guide(db);
        if kind == Kind::Series {
            self.enter(db, false);
        }
    }

    /// Recompute what's on now (the 30-second refresh).
    pub fn refresh_now(&mut self, db: &Db) {
        match &mut self.level.view {
            View::Channels(channels, now) => *now = on_now(db, channels.iter().map(Some)),
            View::Mixed(items, now) => *now = on_now(db, items.iter().map(Item::channel)),
            _ => {}
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

    /// The channel, movie or series under the cursor; in a series, the series.
    fn selected_item(&self) -> Option<Item> {
        let i = self.selected();
        match &self.level.view {
            View::Channels(v, _) => v.get(i).cloned().map(Item::Channel),
            View::Movies(v) => v.get(i).cloned().map(Item::Movie),
            View::Series(v) => v.get(i).cloned().map(Item::Series),
            View::Mixed(v, _) => v.get(i).cloned(),
            View::Episodes { series, .. } => Some(Item::Series(series.clone())),
            View::Kinds | View::Categories(..) => None,
        }
    }

    fn selected_channel(&self) -> Option<Channel> {
        match self.selected_item()? {
            Item::Channel(c) => Some(c),
            _ => None,
        }
    }

    fn is_saved(&self, item: &Item) -> bool {
        self.saved.contains(&item.key())
    }

    /// The episodes of the season on screen.
    fn season_episodes(&self) -> Vec<&Episode> {
        match &self.level.view {
            View::Episodes { all, season, .. } => all.iter().filter(|e| e.season == *season).collect(),
            _ => vec![],
        }
    }

    fn load_guide(&mut self, db: &Db) {
        self.guide = match self.selected_channel() {
            Some(c) => db.guide(c.stream_id, now(), 6).unwrap_or_default(),
            None => vec![],
        };
    }

    pub fn episodes_loaded(&mut self, series_id: i64, loaded: Vec<Episode>) {
        if let View::Episodes { series, all, season, loading } = &mut self.level.view
            && series.series_id == series_id
        {
            *season = seasons(&loaded).first().copied().unwrap_or(1);
            *all = loaded;
            *loading = false;
            self.level.list.select(Some(0));
        }
    }

    pub fn episodes_failed(&mut self) {
        if let View::Episodes { loading, .. } = &mut self.level.view {
            *loading = false;
        }
    }

    /// Which episodes of the open series are on disk, downloading or queued.
    pub fn mark_episodes(&mut self, cfg: &Config, jobs: &[Job]) {
        self.episode_state.clear();
        let View::Episodes { series, all, .. } = &self.level.view else { return };
        // Jobs come newest first: keep each episode's latest try.
        let mut by_id: HashMap<&str, &Job> = HashMap::new();
        for j in jobs.iter().filter(|j| j.unfinished() || j.state == State::Failed) {
            if let Source::Episode { episode_id, .. } = &j.source {
                by_id.entry(episode_id.as_str()).or_insert(j);
            }
        }
        for e in all {
            let on_disk = downloads::episode_output(cfg, &series.name, e).exists();
            let state = match by_id.get(e.episode_id.as_str()) {
                Some(j) if j.state == State::Downloading => Some(EpisodeState::Downloading(j.progress())),
                Some(j) if j.state == State::Waiting => Some(EpisodeState::Waiting),
                Some(j) if j.state == State::Queued => Some(EpisodeState::Queued),
                _ if on_disk => Some(EpisodeState::OnDisk),
                Some(_) => Some(EpisodeState::Failed),
                None => None,
            };
            if let Some(state) = state {
                self.episode_state.insert(e.episode_id.clone(), state);
            }
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

    pub fn on_key(&mut self, key: KeyEvent, db: &Db, restream_live: bool) -> Option<Action> {
        let i = self.selected();
        let lists_channels = matches!(self.level.view, View::Channels(..) | View::Mixed(..));
        match key.code {
            KeyCode::Enter | KeyCode::Right | KeyCode::Char('l') => self.enter(db, key.code == KeyCode::Enter),
            KeyCode::Char('f') => self.selected_item().map(|item| Action::ToggleSaved(item.saved())),
            KeyCode::Char('r') => self.selected_channel().map(Action::RecordMenu),
            KeyCode::Char('x') if restream_live && lists_channels => Some(Action::RestreamStop),
            KeyCode::Char('n') => {
                let channel = self.selected_channel()?;
                let next = self.guide.iter().find(|p| p.start_ts > now())?;
                Some(modal::schedule_action(&channel, next))
            }
            KeyCode::Char('[') | KeyCode::Char(']') => {
                if let View::Episodes { all, season, .. } = &mut self.level.view {
                    let list = seasons(all);
                    let at = list.iter().position(|s| s == season).unwrap_or(0);
                    let next = if key.code == KeyCode::Char(']') { (at + 1).min(list.len().saturating_sub(1)) } else { at.saturating_sub(1) };
                    if let Some(s) = list.get(next) {
                        *season = *s;
                        self.level.list.select(Some(0));
                    }
                }
                None
            }
            KeyCode::Char('d') => match &self.level.view {
                View::Episodes { series, .. } => self.season_episodes().get(i).map(|e| Action::DownloadEpisodes {
                    series: series.name.clone(),
                    episodes: vec![(*e).clone()],
                    what: format!("S{:02}E{:02}", e.season, e.episode_num),
                }),
                _ => match self.selected_item()? {
                    Item::Movie(m) => Some(Action::DownloadMovie(m)),
                    _ => None,
                },
            },
            KeyCode::Char('s') => match &self.level.view {
                View::Episodes { series, season, .. } => {
                    let episodes: Vec<Episode> = self.season_episodes().into_iter().cloned().collect();
                    (!episodes.is_empty()).then(|| Action::DownloadEpisodes { series: series.name.clone(), episodes, what: format!("season {season}") })
                }
                _ => self.selected_channel().map(|channel| Action::RestreamStart { channel, replace: false }),
            },
            KeyCode::Char('D') => match &self.level.view {
                View::Episodes { series, all, .. } if !all.is_empty() => Some(Action::DownloadEpisodes {
                    series: series.name.clone(),
                    episodes: all.clone(),
                    what: format!("all {} seasons", seasons(all).len()),
                }),
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

    /// ↵ plays or opens; → only opens (lists, categories, series).
    fn enter(&mut self, db: &Db, play: bool) -> Option<Action> {
        let i = self.selected();
        match &self.level.view {
            View::Kinds => {
                let kind = *KINDS.get(i)?;
                let categories = match kind {
                    Kind::Live => db.live_categories(),
                    Kind::Movie => db.movie_categories(),
                    Kind::Series => db.series_categories(),
                };
                self.open(View::Categories(kind, categories.unwrap_or_default()), kind_name(kind).into());
                None
            }
            View::Categories(kind, v) => {
                let category = v.get(i)?.clone();
                let view = match (kind, category.id) {
                    (Kind::Live, _) => {
                        let channels = db.channels_in(&category.name, LIMIT).unwrap_or_default();
                        let now = on_now(db, channels.iter().map(Some));
                        View::Channels(channels, now)
                    }
                    (Kind::Movie, Some(id)) => View::Movies(db.movies_in(id, LIMIT).unwrap_or_default()),
                    _ => View::Series(db.series_in(&category.name, LIMIT).unwrap_or_default()),
                };
                self.open(view, category.name);
                self.load_guide(db);
                None
            }
            View::Episodes { series, .. } if play => {
                let name = series.name.clone();
                self.season_episodes().get(i).map(|e| Action::PlayEpisode { series: name, episode: (*e).clone() })
            }
            View::Episodes { .. } => None,
            _ => match self.selected_item()? {
                Item::Series(series) => {
                    self.open_series(db, series);
                    None
                }
                Item::Channel(c) if play => Some(Action::PlayLive(c)),
                Item::Movie(m) if play => Some(Action::PlayMovie(m)),
                _ => None,
            },
        }
    }

    fn open_series(&mut self, db: &Db, series: Series) {
        let all = db.episodes(series.series_id).unwrap_or_default();
        let loading = all.is_empty();
        if loading {
            self.wants_episodes = Some(series.series_id);
        }
        let season = seasons(&all).first().copied().unwrap_or(1);
        let label = series.name.clone();
        self.open(View::Episodes { series, all, season, loading }, label);
    }

    /// `f` in words for the selection: "save" or "remove".
    fn save_word(&self, item: &Item) -> &'static str {
        if self.is_saved(item) { "remove" } else { "save" }
    }

    pub fn hints(&self, restream_live: bool) -> String {
        if self.typing {
            return "type to search  ↵ done  ↑↓ move".into();
        }
        let item = self.selected_item();
        let f = item.as_ref().map(|i| format!("  f {}", self.save_word(i))).unwrap_or_default();
        match (&self.level.view, &item) {
            (View::Kinds | View::Categories(..), _) => "↵ open  / search  ← back".into(),
            (View::Episodes { .. }, _) => format!("↵ watch  [ ] season  d s D download{f}  ← back"),
            (_, Some(Item::Channel(_))) if restream_live => format!("↵ watch  r record  s restream  x stop restream{f}"),
            (_, Some(Item::Channel(_))) => format!("↵ watch  r record  s restream  n next{f}  / search"),
            (_, Some(Item::Movie(_))) => format!("↵ watch  d download{f}  / search"),
            (_, Some(Item::Series(_))) => format!("↵ episodes{f}  / search"),
            (_, None) => "/ search  ← back".into(),
        }
    }

    // ── the list panel ───────────────────────────────────────────────────

    pub fn draw_list(&mut self, f: &mut Frame, area: Rect, focused: bool, status: &Status) {
        let name = match &self.level.label {
            label if label.is_empty() => self.mode.name().to_string(),
            label => format!("{} · {label}", self.mode.name()),
        };
        let title = match &self.level.view {
            View::Episodes { loading: true, .. } => format!("{name} · loading…"),
            _ => name,
        };
        let hint = if self.typing || !self.query.is_empty() { " esc clear " } else { " / search " };
        let block = panel(&title, focused).title_top(Line::from(hint).fg(DIM).right_aligned());
        let inner = block.inner(area);
        f.render_widget(block, area);

        let searching = self.typing || !self.query.is_empty();
        let seasons_row = matches!(self.level.view, View::Episodes { .. });
        let [search, tabs, header, list] = Layout::vertical([
            Constraint::Length(if searching && !seasons_row { 2 } else { 0 }),
            Constraint::Length(if seasons_row { 2 } else { 0 }),
            Constraint::Length(1),
            Constraint::Min(0),
        ])
        .areas(inner);
        if searching && !seasons_row {
            let cursor = if self.typing { Span::styled("▏", Style::new().fg(ACCENT)) } else { Span::raw("") };
            f.render_widget(Line::from(vec![Span::styled("/ ", Style::new().fg(ACCENT).bold()), Span::raw(self.query.clone()), cursor]), search);
        }
        if let View::Episodes { all, season, .. } = &self.level.view {
            let mut spans = vec![];
            for s in seasons(all) {
                let style = if s == *season { Style::new().fg(BG).bg(ACCENT).bold() } else { Style::new().fg(DIM) };
                spans.push(Span::styled(format!(" S{s} "), style));
                spans.push(Span::raw(" "));
            }
            spans.push(dim("  [ ] season"));
            f.render_widget(Paragraph::new(Line::from(spans)).wrap(Wrap { trim: true }), tabs);
        }

        let w = inner.width as usize;
        let (head, items) = self.rows(w, status);
        f.render_widget(Line::from(head).fg(DIM).bold(), header);
        if items.is_empty() {
            f.render_widget(Paragraph::new(self.empty_text()).fg(DIM).wrap(Wrap { trim: true }), list);
            return;
        }
        let widget = List::new(items).highlight_style(selected_style()).highlight_spacing(HighlightSpacing::Never);
        f.render_stateful_widget(widget, list, &mut self.level.list);
    }

    /// "♥ " on a saved row in All, room for it otherwise; nothing in My list.
    fn mark(&self, item: &Item) -> &'static str {
        match self.mode {
            Mode::Mine => "",
            Mode::All if self.is_saved(item) => SAVED,
            Mode::All => "  ",
        }
    }

    /// The column header and one row per item, fitted to `w` columns.
    fn rows(&self, w: usize, status: &Status) -> (String, Vec<ListItem<'static>>) {
        let t = now();
        let dim_style = Style::new().fg(DIM);
        let restreaming = status.restream.channel_name.as_deref().filter(|_| status.restream.live);
        let mark_w = if self.mode == Mode::All { SAVED.chars().count() } else { 0 };
        let (name_w, time_w) = ((w * 2 / 5).clamp(12, 26), 13);
        let show_w = w.saturating_sub(mark_w + name_w + time_w + 2);
        // A channel row; a movie or series in a mixed list uses the same columns.
        let line = |mark: &str, name: &str, show: &str, time: Span<'static>| {
            ListItem::new(Line::from(vec![Span::styled(mark.to_string(), Style::new().fg(BAD)), Span::raw(format!("{}  ", fit(name, name_w))), Span::styled(fit(show, show_w), dim_style), time]))
        };
        let channel_row = |item: &Item, c: &Channel, p: Option<&Program>| {
            let event = event_info(&c.name);
            let name = event.as_ref().map(|e| e.channel.clone()).unwrap_or_else(|| display_name(&c.name).to_string());
            let (show, time) = match (p, &event) {
                (Some(p), _) => {
                    let done = (t - p.start_ts) as f64 / (p.end_ts - p.start_ts).max(1) as f64;
                    (p.title.clone().unwrap_or_default(), format!("{} {:>7}", blocks(done, 5), left(p.end_ts - t)))
                }
                (None, Some(e)) => (e.title.clone(), e.start.map(|s| format!("started {}", s.format("%H:%M"))).unwrap_or_default()),
                (None, None) => (String::new(), String::new()),
            };
            let time = if restreaming == Some(c.name.as_str()) {
                Span::styled(format!("{:>time_w$}", "● restreaming"), Style::new().fg(OK).bold())
            } else {
                Span::styled(format!("{time:>time_w$}"), dim_style)
            };
            line(self.mark(item), &name, &show, time)
        };
        let stars = |r: Option<f64>| rating(r).unwrap_or_default();
        let what = |kind: &str, extra: Option<String>| [Some(kind.to_string()), extra.filter(|e| !e.is_empty())].into_iter().flatten().collect::<Vec<_>>().join(" · ");
        match &self.level.view {
            View::Kinds => {
                let counts = status.database.as_ref();
                let rows = KINDS
                    .iter()
                    .map(|kind| {
                        let n = counts.map(|c| match kind {
                            Kind::Live => c.live,
                            Kind::Movie => c.vod,
                            Kind::Series => c.series,
                        });
                        let count = n.map(|n| format!("{n}")).unwrap_or_default();
                        ListItem::new(Line::from(vec![Span::raw(fit(kind_name(*kind), w.saturating_sub(10))), Span::styled(format!("{count:>10}"), dim_style)]))
                    })
                    .collect();
                ("LIST".into(), rows)
            }
            View::Categories(_, v) => ("CATEGORY".into(), v.iter().map(|c| ListItem::new(fit(&c.name, w))).collect()),
            View::Channels(channels, now_on) => {
                let head = format!("{}{}  {}{:>time_w$}", " ".repeat(mark_w), fit("CHANNEL", name_w), fit("ON NOW", show_w), "TIME");
                let items = channels.iter().zip(now_on).map(|(c, p)| channel_row(&Item::Channel(c.clone()), c, p.as_ref())).collect();
                (head, items)
            }
            View::Mixed(items, now_on) => {
                let middle = if items.iter().any(|i| i.channel().is_some()) { "ON NOW" } else { "" };
                let head = format!("{}{}  {}", " ".repeat(mark_w), fit("NAME", name_w), fit(middle, show_w));
                let rows = items
                    .iter()
                    .zip(now_on)
                    .map(|(item, p)| match item {
                        Item::Channel(c) => channel_row(item, c, p.as_ref()),
                        Item::Movie(m) => line(self.mark(item), &m.name, &what("Movie", m.year.clone()), Span::styled(format!("{:>time_w$}", stars(m.rating)), dim_style)),
                        Item::Series(s) => line(self.mark(item), &s.name, &what("Series", s.genre.clone()), Span::styled(format!("{:>time_w$}", stars(s.rating)), dim_style)),
                    })
                    .collect();
                (head, rows)
            }
            View::Movies(v) => {
                let name_w = w.saturating_sub(14 + mark_w);
                let head = format!("{}{}{:>6}{:>8}", " ".repeat(mark_w), fit("TITLE", name_w), "YEAR", "RATING");
                let items = v
                    .iter()
                    .map(|m| {
                        ListItem::new(Line::from(vec![
                            Span::styled(self.mark(&Item::Movie(m.clone())), Style::new().fg(BAD)),
                            Span::raw(fit(&m.name, name_w)),
                            Span::styled(format!("{:>6}{:>8}", m.year.clone().unwrap_or_default(), stars(m.rating)), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
            View::Series(v) => {
                let genre_w = 18.min(w / 3);
                let name_w = w.saturating_sub(genre_w + 2 + mark_w);
                let head = format!("{}{}  {}", " ".repeat(mark_w), fit("SERIES", name_w), fit("GENRE", genre_w));
                let items = v
                    .iter()
                    .map(|s| {
                        ListItem::new(Line::from(vec![
                            Span::styled(self.mark(&Item::Series(s.clone())), Style::new().fg(BAD)),
                            Span::raw(format!("{}  ", fit(&s.name, name_w))),
                            Span::styled(fit(s.genre.as_deref().unwrap_or(""), genre_w), dim_style),
                        ]))
                    })
                    .collect();
                (head, items)
            }
            View::Episodes { .. } => {
                let (len_w, state_w) = (8, 13);
                let title_w = w.saturating_sub(8 + len_w + state_w);
                let head = format!("{:<8}{}{:>len_w$}{:>state_w$}", "EP", fit("TITLE", title_w), "LENGTH", "STATUS");
                let items = self
                    .season_episodes()
                    .into_iter()
                    .map(|e| {
                        let (state, color): (String, Color) = match self.episode_state.get(&e.episode_id) {
                            Some(EpisodeState::OnDisk) => ("✓ on disk".into(), OK),
                            Some(EpisodeState::Downloading(Some(p))) => (format!("↓ {p:.0}%"), ACCENT),
                            Some(EpisodeState::Downloading(None)) => ("↓ downloading".into(), ACCENT),
                            Some(EpisodeState::Waiting) => ("waiting".into(), WARN),
                            Some(EpisodeState::Queued) => ("queued".into(), DIM),
                            Some(EpisodeState::Failed) => ("✗ failed".into(), BAD),
                            None => (String::new(), DIM),
                        };
                        ListItem::new(Line::from(vec![
                            Span::styled(format!("{:<8}", format!("S{:02}E{:02}", e.season, e.episode_num)), Style::new().fg(ACCENT)),
                            Span::raw(fit(&episode_title(e), title_w)),
                            Span::styled(format!("{:>len_w$}", minutes(e.duration.as_deref())), dim_style),
                            Span::styled(format!("{state:>state_w$}"), Style::new().fg(color)),
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
            (View::Mixed(..), "") if self.mode == Mode::Mine => {
                "Nothing saved yet. In All, press f on a channel, a movie or a series to add it here.".into()
            }
            (_, "") => "Nothing here.".into(),
            (_, q) => format!("Nothing matches “{q}”."),
        }
    }

    // ── the detail panel ─────────────────────────────────────────────────

    pub fn draw_detail(&self, f: &mut Frame, area: Rect, schedules: &[Schedule], status: &Status, watch_url: &str) {
        let block = panel("", false);
        let inner = block.inner(area);
        f.render_widget(block, area);
        let i = self.selected();
        let w = inner.width as usize;

        let (lines, mut actions): (Vec<Line>, Vec<(&str, String)>) = match &self.level.view {
            View::Kinds => match KINDS.get(i) {
                Some(kind) => (
                    vec![Line::from(kind_name(*kind)).bold(), Line::from(dim("Everything your provider lists, by its own categories."))],
                    vec![("↵", "open".to_string())],
                ),
                None => (vec![], vec![]),
            },
            View::Categories(_, v) => match v.get(i) {
                Some(c) => (vec![Line::from(c.name.clone()).bold()], vec![("↵", "open".to_string())]),
                None => (vec![], vec![]),
            },
            View::Episodes { series, all, season, .. } => {
                let in_season = self.season_episodes();
                let on_disk = all.iter().filter(|e| matches!(self.episode_state.get(&e.episode_id), Some(EpisodeState::OnDisk))).count();
                let mut lines = vec![Line::from(dim(format!("{} · SEASON {season}", series.name.to_uppercase())))];
                if let Some(e) = in_season.get(i) {
                    lines.push(Line::from(format!("S{:02}E{:02} · {}", e.season, e.episode_num, episode_title(e))).bold());
                    lines.push(Line::from(dim(meta(vec![e.air_date.clone(), Some(minutes(e.duration.as_deref())).filter(|m| !m.is_empty())]))));
                }
                lines.push(Line::raw(""));
                lines.push(Line::from(dim(format!("{} seasons · {} episodes · {on_disk} on disk", seasons(all).len(), all.len()))));
                let actions = vec![
                    ("↵", "watch".to_string()),
                    ("d", "download episode".to_string()),
                    ("s", format!("download season {season} ({})", in_season.len())),
                    ("D", format!("download all seasons ({})", all.len())),
                ];
                (lines, actions)
            }
            _ => match self.selected_item() {
                Some(Item::Channel(c)) => self.channel_detail(&c, w, schedules, status, watch_url),
                Some(Item::Movie(m)) => movie_detail(&m),
                Some(Item::Series(s)) => series_detail(&s),
                None => (vec![], vec![]),
            },
        };
        if let Some(item) = self.selected_item() {
            actions.push(("f", if self.is_saved(&item) { "remove from My list" } else { "save to My list" }.to_string()));
        }
        let used = chips(f, inner, &actions);
        let text = Rect { height: inner.height.saturating_sub(used), ..inner };
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), text);
    }

    fn channel_detail(&self, c: &Channel, w: usize, schedules: &[Schedule], status: &Status, watch_url: &str) -> (Vec<Line<'static>>, Vec<(&'static str, String)>) {
        let restreaming_here = status.restream.live && status.restream.channel_name.as_deref() == Some(c.name.as_str());
        let mut lines = self.channel_lines(c, w, schedules);
        if restreaming_here {
            let since = status.restream.since.as_deref().and_then(|s| s.get(11..16)).unwrap_or("?");
            lines.push(Line::raw(""));
            lines.push(Line::from(Span::styled("● Restreaming to the family", Style::new().fg(OK).bold())));
            lines.push(Line::from(format!("  {} · since {since}", watch_url.trim_start_matches("https://").trim_end_matches('/'))));
        }
        let actions = if restreaming_here {
            vec![("↵", "watch".to_string()), ("r", "record".to_string()), ("x", "stop the restream".to_string()), ("n", "schedule next".to_string())]
        } else {
            vec![("↵", "watch".to_string()), ("r", "record".to_string()), ("s", "restream".to_string()), ("n", "schedule next".to_string())]
        };
        (lines, actions)
    }

    fn channel_lines(&self, channel: &Channel, w: usize, schedules: &[Schedule]) -> Vec<Line<'static>> {
        let t = now();
        let current = self.guide.first().filter(|p| p.start_ts <= t);
        let mut lines = vec![];
        match (current, event_info(&channel.name)) {
            (Some(p), _) => {
                lines.push(Line::from(dim(channel.name.clone())));
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
            (None, Some(e)) => {
                let what = [Some(e.channel.clone()), e.sport.clone(), e.feed.as_ref().map(|f| format!("{f} feed"))].into_iter().flatten().collect::<Vec<_>>().join(" · ");
                lines.push(Line::from(dim(what.to_uppercase())));
                lines.push(Line::from(e.title.clone()).bold());
                if let Some(s) = e.start {
                    lines.push(Line::from(dim(format!("Started {}", s.format("%a %H:%M")))));
                }
                lines.push(Line::from(dim("Game channels have no TV guide")));
            }
            (None, None) => {
                lines.push(Line::from(dim(channel.category.clone().unwrap_or_default())));
                lines.push(Line::from(channel.name.clone()).bold());
                lines.push(Line::from(dim("No guide information")));
            }
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
            lines.push(Line::from(vec![dim(format!("{}  ", hhmm(p.start_ts))), Span::raw(fit(&title, title_w)), Span::styled(format!(" {rec}"), Style::new().fg(BAD))]));
        }
        lines
    }
}

fn meta(parts: Vec<Option<String>>) -> String {
    parts.into_iter().flatten().collect::<Vec<_>>().join("  ·  ")
}

fn rating(r: Option<f64>) -> Option<String> {
    r.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}"))
}

fn movie_detail(m: &Movie) -> (Vec<Line<'static>>, Vec<(&'static str, String)>) {
    (
        vec![Line::from(dim("MOVIE")), Line::from(m.name.clone()).bold(), Line::from(dim(meta(vec![m.year.clone(), rating(m.rating), m.genre.clone()])))],
        vec![("↵", "watch".to_string()), ("d", "download".to_string())],
    )
}

fn series_detail(s: &Series) -> (Vec<Line<'static>>, Vec<(&'static str, String)>) {
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
    (lines, vec![("↵", "episodes".to_string())])
}

/// "00:28:50" → "29 min"
fn minutes(duration: Option<&str>) -> String {
    let Some(d) = duration else { return String::new() };
    let parts: Vec<i64> = d.split(':').filter_map(|p| p.parse().ok()).collect();
    let secs = match parts.as_slice() {
        [h, m, s] => h * 3600 + m * 60 + s,
        [m, s] => m * 60 + s,
        _ => return String::new(),
    };
    format!("{} min", (secs + 30) / 60)
}

/// "EN - Mr Bates vs The Post Office - S01E01 - Episode 1" → "Episode 1";
/// "EN - Curb Your Enthusiasm - S01E01" → "Episode 1".
fn episode_title(e: &Episode) -> String {
    let code = format!("S{:02}E{:02}", e.season, e.episode_num);
    match e.title.split_once(&format!(" - {code} - ")) {
        Some((_, rest)) if !rest.trim().is_empty() => rest.to_string(),
        _ if e.title.ends_with(&code) || e.title.is_empty() => format!("Episode {}", e.episode_num),
        _ => e.title.clone(),
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
    schedules.iter().any(|s| s.enabled && s.stream_id == stream_id && s.time == time && (s.days.contains(&day) || s.date.as_deref() == Some(date.as_str())))
}
