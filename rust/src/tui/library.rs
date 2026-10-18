//! Library: the download queue, then every finished file, newest first.

use super::modal::Action;
use super::theme::*;
use super::{label_value, move_in, App, Focus, Rows};
use crate::downloads::State;
use crate::library::human_size;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

const SHOW_FILES: usize = 300;

#[derive(Clone)]
enum Key {
    Job(usize),
    File(usize),
}

fn state_label(state: &State) -> (&'static str, Color) {
    match state {
        State::Queued => ("queued", DIM),
        State::Downloading => ("downloading", ACCENT),
        State::Waiting => ("waiting", WARN),
        State::Done => ("done", OK),
        State::Failed => ("failed", BAD),
        State::Cancelled => ("cancelled", DIM),
    }
}

fn when(ts: i64) -> String {
    chrono::DateTime::from_timestamp(ts, 0).map(|t| t.with_timezone(&chrono::Local).format("%a %d %b").to_string()).unwrap_or_default()
}

impl App {
    /// Jobs worth showing: unfinished ones, and the ones that ended in the last day.
    fn visible_jobs(&self) -> Vec<usize> {
        let recent = (chrono::Local::now() - chrono::Duration::hours(24)).format("%Y-%m-%dT%H:%M:%S").to_string();
        (0..self.jobs.len())
            .filter(|&i| {
                let j = &self.jobs[i];
                !matches!(j.state, State::Done | State::Cancelled | State::Failed) || j.finished_at.as_deref().is_some_and(|t| t > recent.as_str())
            })
            .collect()
    }

    fn library_rows(&self, w: usize) -> Rows<Key> {
        let mut rows = Rows::new();
        let jobs = self.visible_jobs();
        if !jobs.is_empty() {
            rows.header("DOWNLOADS");
        }
        for i in jobs {
            let j = &self.jobs[i];
            let (label, color) = state_label(&j.state);
            let size = if j.bytes > 0 { human_size(j.bytes) } else { String::new() };
            rows.row(
                Key::Job(i),
                Line::from(vec![
                    Span::styled(format!("{label:<12}"), Style::new().fg(color)),
                    Span::raw(fit(&j.title, w.saturating_sub(12 + 10))),
                    dim(format!("{size:>10}")),
                ]),
            );
        }

        rows.header("FILES");
        if self.media.is_empty() {
            rows.note("No recordings or downloads on disk yet.");
        }
        for (i, m) in self.media.iter().take(SHOW_FILES).enumerate() {
            rows.row(
                Key::File(i),
                Line::from(vec![
                    dim(format!("{:<10}", m.kind)),
                    Span::raw(fit(&m.name, w.saturating_sub(10 + 22))),
                    dim(format!("{:>10}{:>12}", human_size(m.size), when(m.modified))),
                ]),
            );
        }
        if self.media.len() > SHOW_FILES {
            rows.note(&format!("… and {} older files", self.media.len() - SHOW_FILES));
        }
        rows
    }

    pub(super) fn draw_library(&mut self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let block = panel("Library", self.focus == Focus::List);
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
        let (lines, actions): (Vec<Line>, Vec<(&str, &str)>) = match keys.get(self.library_list.selected().unwrap_or(0)) {
            Some(Key::Job(i)) => match self.jobs.get(*i) {
                Some(j) => {
                    let (label, color) = state_label(&j.state);
                    let mut lines = vec![
                        Line::from(Span::styled(label.to_uppercase(), Style::new().fg(color).bold())),
                        Line::from(j.title.clone()).bold(),
                        Line::raw(""),
                        label_value("So far", human_size(j.bytes), FG),
                        label_value("Saves to", j.output.clone(), FG),
                    ];
                    if j.state == State::Waiting {
                        lines.push(Line::raw(""));
                        lines.push(Line::from(dim("Paused while a recording or the restream uses the connection. It picks up where it stopped.")));
                    }
                    if let Some(e) = &j.error {
                        lines.push(Line::raw(""));
                        lines.push(Line::from(Span::styled(e.clone(), Style::new().fg(BAD))));
                    }
                    let unfinished = matches!(j.state, State::Queued | State::Downloading | State::Waiting);
                    (lines, if unfinished { vec![("x", "cancel")] } else { vec![] })
                }
                None => (vec![], vec![]),
            },
            Some(Key::File(i)) => match self.media.get(*i) {
                Some(m) => (
                    vec![
                        Line::from(dim(m.kind.to_uppercase())),
                        Line::from(m.name.clone()).bold(),
                        Line::raw(""),
                        label_value("Size", human_size(m.size), FG),
                        label_value("Date", when(m.modified), FG),
                        label_value("Folder", m.path.parent().map(|p| p.display().to_string()).unwrap_or_default(), DIM),
                    ],
                    vec![("↵", "play")],
                ),
                None => (vec![], vec![]),
            },
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
        match (key.code, keys.get(self.library_list.selected().unwrap_or(0))?) {
            (KeyCode::Enter, Key::File(i)) => self.media.get(*i).cloned().map(Action::PlayFile),
            (KeyCode::Char('x'), Key::Job(i)) => self.jobs.get(*i).map(|j| Action::CancelDownload(j.id.clone())),
            _ => None,
        }
    }
}
