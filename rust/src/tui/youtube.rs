//! YouTube: type, ↵ to search; then ↵ watches in mpv, `d` downloads the video,
//! `a` only its audio (mp3). Downloads land in the Library.

use super::modal::Action;
use super::theme::*;
use super::{move_in, selected_style};
use crate::youtube::{duration, Video};
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, HighlightSpacing, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;

#[derive(Default)]
pub struct YouTube {
    pub query: String,
    pub typing: bool,
    searched: String,
    videos: Vec<Video>,
    loading: bool,
    list: ListState,
    /// Set when the user pressed ↵ in the box.
    pub wants_search: Option<String>,
}

impl YouTube {
    pub fn is_empty(&self) -> bool {
        self.videos.is_empty() && self.query.is_empty()
    }

    /// Results found from the Search section.
    pub fn show(&mut self, query: &str, videos: Vec<Video>, selected: usize) {
        self.query = query.to_string();
        self.searched = query.to_string();
        self.videos = videos;
        self.loading = false;
        self.typing = false;
        self.list.select(Some(selected));
    }

    pub fn loaded(&mut self, query: &str, videos: Vec<Video>) {
        if query == self.query.trim() {
            self.show(query, videos, 0);
        }
    }

    pub fn failed(&mut self) {
        self.loading = false;
    }

    pub fn on_typing_key(&mut self, key: KeyEvent) {
        match key.code {
            KeyCode::Enter if !self.query.trim().is_empty() => {
                self.wants_search = Some(self.query.trim().to_string());
                self.loading = true;
                self.typing = false;
            }
            KeyCode::Esc => self.typing = false,
            KeyCode::Down if !self.videos.is_empty() => self.typing = false,
            KeyCode::Backspace => {
                self.query.pop();
            }
            KeyCode::Char(c) => self.query.push(c),
            _ => {}
        }
    }

    pub fn on_key(&mut self, key: KeyEvent) -> Option<Action> {
        let video = self.list.selected().and_then(|i| self.videos.get(i)).cloned();
        match key.code {
            KeyCode::Enter => video.map(Action::PlayYouTube),
            KeyCode::Char('d') => video.map(|v| Action::DownloadYouTube { video: v, audio: false }),
            KeyCode::Char('a') => video.map(|v| Action::DownloadYouTube { video: v, audio: true }),
            code => {
                move_in(&mut self.list, code, self.videos.len());
                None
            }
        }
    }

    pub fn hints(&self) -> &'static str {
        if self.typing { "type, then ↵ to search  esc done" } else { "↵ watch  d download  a audio  / new search" }
    }

    pub fn draw(&mut self, f: &mut Frame, list: Rect, detail: Rect, focused: bool, wide: bool) {
        let title = if self.searched.is_empty() { "YouTube".to_string() } else { format!("YouTube · {} results for “{}”", self.videos.len(), self.searched) };
        let block = panel(&title, focused);
        let inner = block.inner(list);
        f.render_widget(block, list);
        let [input, _, header, results] = Layout::vertical([Constraint::Length(3), Constraint::Length(1), Constraint::Length(1), Constraint::Min(0)]).areas(inner);

        let mut spans = vec![Span::styled("▶ ", Style::new().fg(BAD)), Span::raw(self.query.clone())];
        if self.typing && focused {
            spans.push(Span::styled("▏", Style::new().fg(ACCENT)));
        }
        if self.query.is_empty() {
            spans.push(dim("search YouTube, then ↵"));
        }
        let box_style = Style::new().fg(if self.typing && focused { ACCENT } else { BORDER });
        f.render_widget(Paragraph::new(Line::from(spans)).block(Block::bordered().border_type(BorderType::Rounded).border_style(box_style)), input);

        let w = results.width as usize;
        let (chan_w, len_w) = (22.min(w / 3), 9);
        let title_w = w.saturating_sub(chan_w + len_w + 1);
        f.render_widget(Line::from(format!("{}{} {:>len_w$}", fit("TITLE", title_w), fit("CHANNEL", chan_w), "LENGTH")).fg(DIM).bold(), header);
        if self.loading {
            f.render_widget(Paragraph::new("Searching YouTube…").fg(DIM), results);
        } else if self.videos.is_empty() {
            let text = if self.searched.is_empty() { "Type what you're looking for and press ↵." } else { "Nothing found." };
            f.render_widget(Paragraph::new(text).fg(DIM), results);
        } else {
            let items: Vec<ListItem> = self
                .videos
                .iter()
                .map(|v| ListItem::new(Line::from(vec![Span::raw(fit(&v.title, title_w)), dim(fit(&v.channel, chan_w)), dim(format!(" {:>len_w$}", duration(v.duration)))])))
                .collect();
            let widget = List::new(items).highlight_style(selected_style()).highlight_spacing(HighlightSpacing::Never);
            f.render_stateful_widget(widget, results, &mut self.list);
        }

        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let (lines, actions): (Vec<Line>, Vec<(&str, String)>) = match self.list.selected().and_then(|i| self.videos.get(i)) {
            Some(v) if !self.typing => (
                vec![
                    Line::from(dim(v.channel.to_uppercase())),
                    Line::from(v.title.clone()).bold(),
                    Line::from(dim(duration(v.duration))),
                    Line::raw(""),
                    Line::from(dim(v.url.clone())),
                ],
                vec![("↵", "watch".to_string()), ("d", "download video".to_string()), ("a", "audio only (mp3)".to_string())],
            ),
            _ => (vec![Line::from("YouTube").bold(), Line::raw(""), Line::from(dim("Watch in mpv, or download the video or just its audio. Downloads go to the Library."))], vec![]),
        };
        let used = chips(f, area, &actions);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), Rect { height: area.height.saturating_sub(used), ..area });
    }
}
