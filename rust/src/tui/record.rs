//! Record: what's recording or restreaming now, the schedules, the latest recordings.

use super::modal::Action;
use super::theme::*;
use super::{label_value, move_in, App, Focus, Rows};
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

#[derive(Clone)]
enum Key {
    Recording(usize),
    Restream,
    Schedule(usize),
    Run(usize),
}

impl App {
    fn record_rows(&self, w: usize) -> Rows<Key> {
        let mut rows = Rows::new();
        rows.header("NOW");
        for (i, r) in self.status.recording.iter().enumerate() {
            rows.row(Key::Recording(i), Line::from(vec![Span::styled(" REC ", Style::new().fg(BG).bg(BAD).bold()), Span::raw(format!("  {}", fit(&r.what, w.saturating_sub(7))))]));
        }
        if self.status.restream.live {
            let name = self.status.restream.channel_name.clone().unwrap_or_default();
            rows.row(Key::Restream, Line::from(vec![Span::styled(" LIVE ", Style::new().fg(BG).bg(OK).bold()), Span::raw(format!("  {}", fit(&name, w.saturating_sub(8))))]));
        }
        if self.status.recording.is_empty() && !self.status.restream.live {
            rows.note("Nothing is recording or restreaming.");
        }

        rows.header("SCHEDULED");
        if self.schedules.is_empty() {
            rows.note("Nothing scheduled. In Live, press r on a channel.");
        }
        for (i, s) in self.schedules.iter().enumerate() {
            let past = s.days.is_empty() && s.next_run.is_none();
            let (dot, color) = if past { ("○ ", DIM) } else if s.enabled { ("● ", OK) } else { ("○ ", WARN) };
            let when = if past { "past".to_string() } else if s.days.is_empty() { s.date.clone().unwrap_or_default() } else { s.days.join(" ") };
            let name = s.show_name.clone().unwrap_or_else(|| s.channel_name.clone());
            let when_w = 22.min(w / 2);
            rows.row(
                Key::Schedule(i),
                Line::from(vec![
                    Span::styled(dot, Style::new().fg(color)),
                    Span::raw(fit(&name, w.saturating_sub(when_w + 2))),
                    dim(format!("{:>when_w$}", format!("{when} {}", s.time))),
                ]),
            );
        }

        rows.header("RECENT RECORDINGS");
        if self.runs.is_empty() {
            rows.note("No recordings yet.");
        }
        for (i, r) in self.runs.iter().enumerate() {
            let (mark, color) = run_mark(&r.status);
            let name = r.show_name.clone().or_else(|| r.channel_name.clone()).unwrap_or_else(|| "?".into());
            let when = started(r.start_time.as_deref());
            rows.row(
                Key::Run(i),
                Line::from(vec![
                    Span::styled(format!("{mark} "), Style::new().fg(color).bold()),
                    dim(format!("{when}  ")),
                    Span::raw(fit(&name, w.saturating_sub(when.chars().count() + 4 + 10))),
                    dim(format!("{:>10}", r.recorded)),
                ]),
            );
        }
        rows
    }

    pub(super) fn draw_record(&mut self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let block = panel("Record", self.focus == Focus::List);
        let inner = block.inner(list);
        f.render_widget(block, list);
        let rows = self.record_rows(inner.width as usize);
        let keys = rows.keys();
        rows.render(f, inner, &mut self.record_list);
        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let selected = keys.get(self.record_list.selected().unwrap_or(0)).cloned();
        let (lines, actions): (Vec<Line>, Vec<(&str, &str)>) = match selected {
            Some(Key::Recording(i)) => match self.status.recording.get(i) {
                Some(r) => (
                    vec![Line::from(dim("RECORDING NOW")), Line::from(r.what.clone()).bold(), Line::raw(""), Line::from(dim(r.unit.clone()))],
                    vec![("x", "stop")],
                ),
                None => (vec![], vec![]),
            },
            Some(Key::Restream) => (
                vec![
                    Line::from(dim("RESTREAMING")),
                    Line::from(self.status.restream.channel_name.clone().unwrap_or_default()).bold(),
                    Line::from(dim(format!("since {}", self.status.restream.since.as_deref().and_then(|s| s.get(11..16)).unwrap_or("?")))),
                    Line::raw(""),
                    label_value("Watch at", self.cfg.watch_url.clone(), ACCENT),
                ],
                vec![("x", "stop restream")],
            ),
            Some(Key::Schedule(i)) => match self.schedules.get(i) {
                Some(s) => (
                    vec![
                        Line::from(dim(if s.enabled { "SCHEDULED" } else { "PAUSED" })),
                        Line::from(s.show_name.clone().unwrap_or_else(|| s.channel_name.clone())).bold(),
                        Line::raw(""),
                        label_value("Channel", s.channel_name.clone(), FG),
                        label_value("When", if s.days.is_empty() { format!("once, {} {}", s.date.clone().unwrap_or_default(), s.time) } else { format!("every {} at {}", s.days.join("/"), s.time) }, FG),
                        label_value("Length", format!("{} min", s.duration_min), FG),
                        label_value("Next", if s.enabled { s.next_run.clone().unwrap_or_else(|| "not planned".into()) } else { "paused".into() }, FG),
                        label_value("Goes to", s.show_name.as_ref().map(|n| format!("Jellyfin “{n}”")).unwrap_or_else(|| "recordings folder".into()), FG),
                        label_value("Made by", s.created_by.clone().unwrap_or_default(), DIM),
                    ],
                    vec![("space", if s.enabled { "pause" } else { "resume" }), ("d", "delete")],
                ),
                None => (vec![], vec![]),
            },
            Some(Key::Run(i)) => match self.runs.get(i) {
                Some(r) => {
                    let (mark, color) = run_mark(&r.status);
                    let mut lines = vec![
                        Line::from(Span::styled(format!("{mark} {}", r.status.to_uppercase()), Style::new().fg(color).bold())),
                        Line::from(r.show_name.clone().or_else(|| r.channel_name.clone()).unwrap_or_default()).bold(),
                        Line::from(dim(started(r.start_time.as_deref()))),
                        Line::raw(""),
                        label_value("Recorded", format!("{} of {}", r.recorded, r.planned), FG),
                    ];
                    if let Some(p) = &r.episode_path {
                        lines.push(label_value("In Jellyfin", p.rsplit('/').next().unwrap_or(p).to_string(), FG));
                    }
                    if !r.problems.is_empty() {
                        lines.push(Line::raw(""));
                        lines.push(heading("WHAT HAPPENED"));
                        for p in r.problems.iter().take(8) {
                            lines.push(Line::from(dim(format!("· {p}"))));
                        }
                    }
                    (lines, vec![])
                }
                None => (vec![], vec![]),
            },
            None => (vec![], vec![]),
        };
        let used = chips(f, area, &actions);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), Rect { height: area.height.saturating_sub(used), ..area });
    }

    pub(super) fn record_key(&mut self, key: KeyEvent) -> Option<Action> {
        let keys = self.record_rows(80).keys();
        if move_in(&mut self.record_list, key.code, keys.len()) {
            return None;
        }
        match (key.code, keys.get(self.record_list.selected().unwrap_or(0))?.clone()) {
            (KeyCode::Char('x'), Key::Recording(i)) => self.status.recording.get(i).map(|r| Action::StopRecording(r.unit.clone())),
            (KeyCode::Char('x'), Key::Restream) => Some(Action::RestreamStop),
            (KeyCode::Char(' '), Key::Schedule(i)) => self.schedules.get(i).map(|s| Action::PauseSchedule { id: s.id.clone(), enabled: !s.enabled }),
            (KeyCode::Char('d'), Key::Schedule(i)) => self.schedules.get(i).map(|s| Action::DeleteSchedule(s.id.clone())),
            _ => None,
        }
    }
}

fn run_mark(status: &str) -> (&'static str, Color) {
    match status {
        "ok" => ("✓", OK),
        "warning" => ("!", WARN),
        "failed" => ("✗", BAD),
        "running" => ("●", ACCENT),
        "stopped" => ("■", WARN),
        _ => ("?", DIM),
    }
}

/// "2026-10-15T18:29:57" → "Thu 15 Oct 18:29"
fn started(text: Option<&str>) -> String {
    text.and_then(|t| chrono::NaiveDateTime::parse_from_str(t, "%Y-%m-%dT%H:%M:%S").ok())
        .map(|t| t.format("%a %d %b %H:%M").to_string())
        .unwrap_or_else(|| text.unwrap_or("?").to_string())
}
