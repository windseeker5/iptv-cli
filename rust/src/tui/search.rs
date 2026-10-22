//! Search: one box for everything. Type, and series, movies and live channels
//! that match show up grouped; YouTube is one ↵ away. ↵ on a result opens it in
//! its own section, where all its actions are.

use super::theme::*;
use super::watch::Mode;
use super::{move_in, App, Focus, Rows, Section};
use crate::db::{Channel, Db, Movie, Series};
use crate::youtube::{self, Video};
use ratatui::crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, ListState, Paragraph, Wrap};
use ratatui::Frame;

const PER_GROUP: usize = 6;

#[derive(Default)]
enum YouTubeState {
    #[default]
    NotAsked,
    Loading,
    Done(Vec<Video>),
}

#[derive(Default)]
pub struct Search {
    pub query: String,
    list: ListState,
    series: Vec<Series>,
    movies: Vec<Movie>,
    channels: Vec<Channel>,
    youtube: YouTubeState,
    /// Set when the user asked to search YouTube too.
    pub wants_youtube: Option<String>,
}

#[derive(Clone)]
enum Hit {
    Series(usize),
    Movie(usize),
    Channel(usize),
    AskYouTube,
    YouTube(usize),
}

impl Search {
    fn update(&mut self, db: &Db) {
        let q = self.query.trim();
        self.youtube = YouTubeState::NotAsked;
        self.list.select(Some(0));
        if q.chars().count() < 2 {
            self.series.clear();
            self.movies.clear();
            self.channels.clear();
            return;
        }
        self.series = db.search_series(q, PER_GROUP).unwrap_or_default();
        self.movies = db.search_movies(q, PER_GROUP).unwrap_or_default();
        self.channels = db.search_channels(q, PER_GROUP).unwrap_or_default();
    }

    pub fn youtube_loaded(&mut self, query: &str, videos: Vec<Video>) {
        if query == self.query.trim() {
            self.youtube = YouTubeState::Done(videos);
        }
    }

    pub fn youtube_failed(&mut self) {
        if matches!(self.youtube, YouTubeState::Loading) {
            self.youtube = YouTubeState::NotAsked;
        }
    }

    fn rows(&self, w: usize) -> Rows<Hit> {
        let mut rows = Rows::new();
        let q = self.query.trim();
        if q.chars().count() < 2 {
            rows.note("Type a channel, a show, a movie or a series.");
            rows.note("Example: noovo · hockey · curb · dune");
            return rows;
        }
        let pair = |a: &str, b: &str| {
            let b_w = 22.min(w / 3);
            Line::from(vec![Span::raw(fit(a, w.saturating_sub(b_w + 1))), dim(format!(" {}", fit(b, b_w)))])
        };
        rows.header(&format!("SERIES · {}", count(self.series.len())));
        for (i, s) in self.series.iter().enumerate() {
            rows.row(Hit::Series(i), pair(&s.name, s.genre.as_deref().unwrap_or("")));
        }
        rows.header(&format!("MOVIES · {}", count(self.movies.len())));
        for (i, m) in self.movies.iter().enumerate() {
            rows.row(Hit::Movie(i), pair(&m.name, m.year.as_deref().unwrap_or("")));
        }
        rows.header(&format!("LIVE CHANNELS · {}", count(self.channels.len())));
        for (i, c) in self.channels.iter().enumerate() {
            let (name, what) = match crate::db::event_info(&c.name) {
                Some(e) => (format!("{} · {}", e.channel, e.title), e.sport.unwrap_or_default()),
                None => (c.name.clone(), c.category.clone().unwrap_or_default()),
            };
            rows.row(Hit::Channel(i), pair(&name, &what));
        }
        rows.header("YOUTUBE");
        match &self.youtube {
            YouTubeState::NotAsked => rows.row(Hit::AskYouTube, Line::from(vec![Span::styled("↵ ", Style::new().fg(ACCENT).bold()), Span::raw(format!("search YouTube for “{q}”"))])),
            YouTubeState::Loading => rows.note("Searching YouTube…"),
            YouTubeState::Done(videos) if videos.is_empty() => rows.note("Nothing on YouTube."),
            YouTubeState::Done(videos) => {
                for (i, v) in videos.iter().enumerate() {
                    rows.row(Hit::YouTube(i), pair(&v.title, &format!("{} · {}", youtube::duration(v.duration), v.channel)));
                }
            }
        }
        rows
    }
}

/// "0", "1", … "6+" (the search stops at six per group).
fn count(n: usize) -> String {
    if n >= PER_GROUP { format!("{PER_GROUP}+") } else { n.to_string() }
}

impl App {
    pub(super) fn search_key(&mut self, key: KeyEvent) {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        match key.code {
            KeyCode::Esc if self.search.query.is_empty() => self.focus = Focus::Sidebar,
            KeyCode::Esc => {
                self.search.query.clear();
                self.search.update(&self.db);
            }
            KeyCode::Left if self.search.query.is_empty() => self.focus = Focus::Sidebar,
            KeyCode::Backspace => {
                self.search.query.pop();
                self.search.update(&self.db);
            }
            KeyCode::Char(c) if !ctrl => {
                self.search.query.push(c);
                self.search.update(&self.db);
            }
            KeyCode::Up | KeyCode::Down | KeyCode::PageUp | KeyCode::PageDown => {
                let n = self.search.rows(80).keys().len();
                move_in(&mut self.search.list, key.code, n);
            }
            KeyCode::Enter => self.open_hit(),
            _ => {}
        }
    }

    fn open_hit(&mut self) {
        let keys = self.search.rows(80).keys();
        let Some(hit) = keys.get(self.search.list.selected().unwrap_or(0)).cloned() else { return };
        let query = self.search.query.trim().to_string();
        let (section, mode, id) = match hit {
            Hit::Series(i) => (Section::Series, Mode::Series, self.search.series[i].series_id),
            Hit::Movie(i) => (Section::Movies, Mode::Movies, self.search.movies[i].stream_id),
            Hit::Channel(i) => (Section::Live, Mode::Live, self.search.channels[i].stream_id),
            Hit::AskYouTube => {
                self.search.youtube = YouTubeState::Loading;
                self.search.wants_youtube = Some(query);
                self.start_pending();
                return;
            }
            Hit::YouTube(i) => {
                if let YouTubeState::Done(videos) = &self.search.youtube {
                    self.youtube.show(&query, videos.clone(), i);
                }
                self.open_section(Section::YouTube);
                return;
            }
        };
        self.open_section(section);
        self.watch.show(mode, &query, id, &self.db, &self.cfg);
        self.start_pending();
    }

    pub(super) fn draw_search(&mut self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let focused = self.focus == Focus::List;
        let block = panel("Search", focused);
        let inner = block.inner(list);
        f.render_widget(block, list);
        let [input, _, results] = Layout::vertical([Constraint::Length(3), Constraint::Length(1), Constraint::Min(0)]).areas(inner);

        let glyph = if self.cfg.icons { format!("{} ", icon::SEARCH) } else { "/ ".into() };
        let mut spans = vec![Span::styled(glyph, Style::new().fg(ACCENT)), Span::raw(self.search.query.clone())];
        if focused {
            spans.push(Span::styled("▏", Style::new().fg(ACCENT)));
        }
        if self.search.query.is_empty() {
            spans.push(dim("live · movies · series · YouTube"));
        }
        let box_style = Style::new().fg(if focused { ACCENT } else { BORDER });
        f.render_widget(Paragraph::new(Line::from(spans)).block(Block::bordered().border_type(BorderType::Rounded).border_style(box_style)), input);

        let rows = self.search.rows(results.width as usize);
        let keys = rows.keys();
        rows.render(f, results, &mut self.search.list);

        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let s = &self.search;
        let (lines, actions): (Vec<Line>, Vec<(&str, String)>) = match keys.get(s.list.selected().unwrap_or(0)) {
            Some(Hit::Series(i)) => {
                let x = &s.series[*i];
                let mut lines = vec![Line::from(dim(x.category.clone().unwrap_or_else(|| "SERIES".into()))), Line::from(x.name.clone()).bold()];
                lines.push(Line::from(dim([x.genre.clone(), x.rating.filter(|r| *r > 0.0).map(|r| format!("★ {r:.1}"))].into_iter().flatten().collect::<Vec<_>>().join(" · "))));
                if let Some(plot) = &x.plot {
                    lines.push(Line::raw(""));
                    lines.push(Line::raw(plot.clone()));
                }
                (lines, vec![("↵", "episodes".to_string())])
            }
            Some(Hit::Movie(i)) => {
                let m = &s.movies[*i];
                (vec![Line::from(dim("MOVIE")), Line::from(m.name.clone()).bold(), Line::from(dim(m.genre.clone().unwrap_or_default()))], vec![("↵", "open in Movies".to_string())])
            }
            Some(Hit::Channel(i)) => {
                let c = &s.channels[*i];
                let lines = match crate::db::event_info(&c.name) {
                    Some(e) => vec![Line::from(dim(format!("{} · {}", e.channel, e.sport.unwrap_or_default()).to_uppercase())), Line::from(e.title).bold()],
                    None => vec![Line::from(dim(c.category.clone().unwrap_or_default())), Line::from(c.name.clone()).bold()],
                };
                (lines, vec![("↵", "open in Live".to_string())])
            }
            Some(Hit::AskYouTube) => (vec![Line::from("YouTube").bold(), Line::from(dim("Takes a second or two."))], vec![("↵", "search YouTube".to_string())]),
            Some(Hit::YouTube(i)) => match &s.youtube {
                YouTubeState::Done(videos) => {
                    let v = &videos[*i];
                    (vec![Line::from(dim(v.channel.to_uppercase())), Line::from(v.title.clone()).bold(), Line::from(dim(youtube::duration(v.duration)))], vec![("↵", "open in YouTube".to_string())])
                }
                _ => (vec![], vec![]),
            },
            None => (vec![Line::from("Search everything at once").bold(), Line::raw(""), Line::from(dim("Results open in their own section, where you can watch, record, restream or download."))], vec![]),
        };
        let used = chips(f, area, &actions);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), Rect { height: area.height.saturating_sub(used), ..area });
    }
}
