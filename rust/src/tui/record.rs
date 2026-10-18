//! The Record tab: what's recording or restreaming now, the schedules, and the
//! latest recordings from the run diary.

use super::modal::Action;
use super::{move_in, App, Rows, ACCENT, BAD, DIM, OK, WARN};
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span, Text};
use ratatui::Frame;

#[derive(Clone)]
enum Key {
    Recording(String),
    Restream,
    Schedule { id: String, enabled: bool },
    Run,
}

impl App {
    fn record_rows(&self) -> Rows<Key> {
        let mut rows = Rows::new();
        rows.header("NOW");
        for r in &self.status.recording {
            rows.row(
                Key::Recording(r.unit.clone()),
                Line::from(vec![Span::styled("● REC   ", Style::new().fg(BAD).bold()), Span::raw(r.what.clone())]),
            );
        }
        if self.status.restream.live {
            let since = self.status.restream.since.as_deref().and_then(|s| s.get(11..16)).unwrap_or("?");
            let name = self.status.restream.channel_name.clone().unwrap_or_default();
            rows.row(
                Key::Restream,
                Line::from(vec![
                    Span::styled("● LIVE  ", Style::new().fg(OK).bold()),
                    Span::raw(name),
                    Span::styled(format!("   since {since} · {}", self.cfg.watch_url), Style::new().fg(DIM)),
                ]),
            );
        }
        if self.status.recording.is_empty() && !self.status.restream.live {
            rows.note("Nothing is recording or restreaming. Pick a channel in Watch and press r or s.");
        }

        rows.header("SCHEDULED");
        if self.schedules.is_empty() {
            rows.note("Nothing scheduled.");
        }
        for s in &self.schedules {
            let (dot, color) = if s.enabled { ("● ", OK) } else { ("○ ", DIM) };
            let next = if s.enabled { s.next_run.clone().unwrap_or_else(|| "not planned".into()) } else { "paused".into() };
            rows.row(
                Key::Schedule { id: s.id.clone(), enabled: s.enabled },
                Text::from(vec![
                    Line::from(vec![Span::styled(dot, Style::new().fg(color)), Span::raw(s.what.clone())]),
                    Line::from(format!("  next {next}")).fg(DIM),
                ]),
            );
        }

        rows.header("RECENT RECORDINGS");
        if self.runs.is_empty() {
            rows.note("No recordings yet.");
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
            let mut lines = vec![Line::from(vec![
                Span::styled(mark, Style::new().fg(color).bold()),
                Span::styled(format!("{:<18}", started(r.start_time.as_deref())), Style::new().fg(DIM)),
                Span::raw(name),
                Span::styled(format!("   {} / {}", r.recorded, r.planned), Style::new().fg(DIM)),
            ])];
            if let Some(problem) = r.problems.first() {
                lines.push(Line::from(format!("  {problem}")).fg(DIM));
            }
            rows.row(Key::Run, Text::from(lines));
        }
        rows
    }

    pub(super) fn draw_record(&mut self, f: &mut Frame, area: Rect) {
        let rows = self.record_rows();
        rows.render(f, area, &mut self.record_list);
    }

    pub(super) fn record_key(&mut self, key: KeyEvent) -> Option<Action> {
        let keys = self.record_rows().keys();
        if move_in(&mut self.record_list, key.code, keys.len()) {
            return None;
        }
        let selected = keys.get(self.record_list.selected().unwrap_or(0))?.clone();
        match (key.code, selected) {
            (KeyCode::Char('x'), Key::Recording(unit)) => Some(Action::StopRecording(unit)),
            (KeyCode::Char('x'), Key::Restream) => Some(Action::RestreamStop),
            (KeyCode::Char(' '), Key::Schedule { id, enabled }) => Some(Action::PauseSchedule { id, enabled: !enabled }),
            (KeyCode::Char('d'), Key::Schedule { id, .. }) => Some(Action::DeleteSchedule(id)),
            _ => None,
        }
    }
}

/// "2026-10-15T18:29:57" → "Thu 15 Oct 18:29"
fn started(text: Option<&str>) -> String {
    text.and_then(|t| chrono::NaiveDateTime::parse_from_str(t, "%Y-%m-%dT%H:%M:%S").ok())
        .map(|t| t.format("%a %d %b %H:%M").to_string())
        .unwrap_or_else(|| text.unwrap_or("?").to_string())
}
