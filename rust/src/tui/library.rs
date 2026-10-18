//! The Library tab: the download queue, then every finished file, newest first.

use super::modal::Action;
use super::{move_in, App, Rows, ACCENT, BAD, DIM, OK, WARN};
use crate::downloads::State;
use crate::library::human_size;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span, Text};
use ratatui::Frame;

const SHOW_FILES: usize = 300;

#[derive(Clone)]
enum Key {
    Job(String),
    File(usize),
}

impl App {
    fn library_rows(&self) -> Rows<Key> {
        let mut rows = Rows::new();
        let day_ago = chrono::Local::now() - chrono::Duration::hours(24);
        let recent = day_ago.format("%Y-%m-%dT%H:%M:%S").to_string();
        let jobs: Vec<_> = self
            .jobs
            .iter()
            .filter(|j| !matches!(j.state, State::Done | State::Cancelled | State::Failed) || j.finished_at.as_deref().is_some_and(|t| t > recent.as_str()))
            .collect();
        if !jobs.is_empty() {
            rows.header("DOWNLOADS");
        }
        for j in jobs {
            let (label, color) = match j.state {
                State::Queued => ("queued     ", DIM),
                State::Downloading => ("downloading", ACCENT),
                State::Waiting => ("waiting    ", WARN),
                State::Done => ("done       ", OK),
                State::Failed => ("failed     ", BAD),
                State::Cancelled => ("cancelled  ", DIM),
            };
            let mut lines = vec![Line::from(vec![
                Span::styled(format!("{label}  "), Style::new().fg(color)),
                Span::raw(j.title.clone()),
                Span::styled(if j.bytes > 0 { format!("   {}", human_size(j.bytes)) } else { String::new() }, Style::new().fg(DIM)),
            ])];
            if j.state == State::Waiting {
                lines.push(Line::from("             paused while a recording or the restream uses the connection").fg(DIM));
            }
            if let Some(e) = &j.error {
                lines.push(Line::from(format!("             {e}")).fg(DIM));
            }
            rows.row(Key::Job(j.id.clone()), Text::from(lines));
        }

        rows.header("FILES");
        if self.media.is_empty() {
            rows.note("No recordings or downloads on disk yet.");
        }
        for (i, m) in self.media.iter().take(SHOW_FILES).enumerate() {
            let when = chrono::DateTime::from_timestamp(m.modified, 0)
                .map(|t| t.with_timezone(&chrono::Local).format("%d %b %H:%M").to_string())
                .unwrap_or_default();
            rows.row(
                Key::File(i),
                Line::from(vec![
                    Span::styled(format!("{:<10}", m.kind), Style::new().fg(DIM)),
                    Span::raw(m.name.clone()),
                    Span::styled(format!("   {}  {when}", human_size(m.size)), Style::new().fg(DIM)),
                ]),
            );
        }
        if self.media.len() > SHOW_FILES {
            rows.note(&format!("… and {} older files", self.media.len() - SHOW_FILES));
        }
        rows
    }

    pub(super) fn draw_library(&mut self, f: &mut Frame, area: Rect) {
        let rows = self.library_rows();
        rows.render(f, area, &mut self.library_list);
    }

    pub(super) fn library_key(&mut self, key: KeyEvent) -> Option<Action> {
        let keys = self.library_rows().keys();
        if move_in(&mut self.library_list, key.code, keys.len()) {
            return None;
        }
        match (key.code, keys.get(self.library_list.selected().unwrap_or(0))?.clone()) {
            (KeyCode::Enter, Key::File(i)) => self.media.get(i).cloned().map(Action::PlayFile),
            (KeyCode::Char('x'), Key::Job(id)) => Some(Action::CancelDownload(id)),
            _ => None,
        }
    }
}
