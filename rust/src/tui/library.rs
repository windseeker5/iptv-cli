//! Library: every finished file (recordings, downloads, YouTube, Jellyfin TV
//! shows), newest first.

use super::modal::Action;
use super::theme::*;
use super::{label_value, move_in, App, Focus, Rows};
use crate::library::human_size;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::Stylize;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

const SHOW_FILES: usize = 300;

fn when(ts: i64) -> String {
    chrono::DateTime::from_timestamp(ts, 0).map(|t| t.with_timezone(&chrono::Local).format("%a %d %b").to_string()).unwrap_or_default()
}

impl App {
    fn library_rows(&self, w: usize) -> Rows<usize> {
        let mut rows = Rows::new();
        if self.media.is_empty() {
            rows.note("No recordings or downloads on disk yet.");
        }
        for (i, m) in self.media.iter().take(SHOW_FILES).enumerate() {
            rows.row(
                i,
                Line::from(vec![
                    dim(format!("{:<11}", m.kind)),
                    Span::raw(fit(&m.name, w.saturating_sub(11 + 23))),
                    dim(format!("{:>10}{:>13}", human_size(m.size), when(m.modified))),
                ]),
            );
        }
        if self.media.len() > SHOW_FILES {
            rows.note(&format!("… and {} older files", self.media.len() - SHOW_FILES));
        }
        rows
    }

    pub(super) fn draw_library(&mut self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let block = panel(&format!("Library · {} files", self.media.len()), self.focus == Focus::List);
        let inner = block.inner(list);
        f.render_widget(block, list);
        let rows = self.library_rows(inner.width as usize);
        let keys = rows.keys();
        rows.render(f, inner, &mut self.library_list);
        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let (lines, actions): (Vec<Line>, Vec<(&str, String)>) = match keys.get(self.library_list.selected().unwrap_or(0)).and_then(|i| self.media.get(*i)) {
            Some(m) => (
                vec![
                    Line::from(dim(m.kind.to_uppercase())),
                    Line::from(m.name.clone()).bold(),
                    Line::raw(""),
                    label_value("Size", human_size(m.size), FG),
                    label_value("Date", when(m.modified), FG),
                    label_value("Folder", m.path.parent().map(|p| p.display().to_string()).unwrap_or_default(), DIM),
                ],
                vec![("↵", "play".to_string())],
            ),
            None => (vec![], vec![]),
        };
        let used = chips(f, area, &actions);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), Rect { height: area.height.saturating_sub(used), ..area });
    }

    pub(super) fn library_key(&mut self, key: KeyEvent) -> Option<Action> {
        let keys = self.library_rows(80).keys();
        if move_in(&mut self.library_list, key.code, keys.len()) {
            return None;
        }
        match key.code {
            KeyCode::Enter => keys.get(self.library_list.selected().unwrap_or(0)).and_then(|i| self.media.get(*i)).cloned().map(Action::PlayFile),
            _ => None,
        }
    }
}
