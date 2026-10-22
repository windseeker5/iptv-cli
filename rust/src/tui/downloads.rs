//! Downloads: the queue (one at a time), then what finished recently.

use super::modal::Action;
use super::theme::*;
use super::{label_value, move_in, App, Focus, Rows};
use crate::downloads::{Source, State};
use crate::library::human_size;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

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

impl App {
    fn downloads_rows(&self, w: usize) -> Rows<usize> {
        let mut rows = Rows::new();
        let week_ago = (chrono::Local::now() - chrono::Duration::days(7)).format("%Y-%m-%dT%H:%M:%S").to_string();
        // The queue in the order it will run, then the last week's finished ones.
        let mut queue: Vec<usize> = (0..self.jobs.len()).filter(|&i| self.jobs[i].unfinished()).collect();
        queue.sort_by(|a, b| self.jobs[*a].id.cmp(&self.jobs[*b].id));
        let finished: Vec<usize> = (0..self.jobs.len())
            .filter(|&i| !self.jobs[i].unfinished() && self.jobs[i].finished_at.as_deref().is_some_and(|t| t > week_ago.as_str()))
            .collect();

        rows.header(&format!("IN THE QUEUE · {}", queue.len()));
        if queue.is_empty() {
            rows.note("Nothing to download. Press d on a movie, an episode or a YouTube video.");
        }
        for i in queue {
            rows.row(i, self.job_line(i, w));
        }
        if !finished.is_empty() {
            rows.header("FINISHED THIS WEEK");
            for i in finished {
                rows.row(i, self.job_line(i, w));
            }
        }
        rows
    }

    /// "▰▰▱▱▱▱▱▱  25%  912.4 MB / 3.4 GB" while it moves; the size once done.
    fn job_line(&self, i: usize, w: usize) -> Line<'static> {
        let j = &self.jobs[i];
        let (label, color) = state_label(&j.state);
        let progress_w = 34.min(w / 2);
        let of = |total: u64| format!("{} / {}", human_size(j.bytes), human_size(total));
        let (bar, text) = match (j.state.clone(), j.progress(), j.real_total()) {
            (State::Downloading | State::Waiting, Some(p), Some(total)) => (blocks(p / 100.0, 8), format!(" {p:>3.0}%  {}", of(total))),
            (State::Downloading | State::Waiting, Some(p), None) => (blocks(p / 100.0, 8), format!(" {p:>3.0}%")),
            (State::Downloading | State::Waiting, None, _) if j.bytes > 0 => (String::new(), human_size(j.bytes)),
            (State::Done, _, _) => (String::new(), human_size(j.bytes)),
            _ => (String::new(), String::new()),
        };
        let progress = format!("{bar}{text}");
        let pad = progress_w.saturating_sub(progress.chars().count());
        Line::from(vec![
            Span::styled(format!("{label:<12}"), Style::new().fg(color)),
            Span::raw(fit(&j.title, w.saturating_sub(12 + progress_w + 1))),
            Span::raw(" ".repeat(pad + 1)),
            Span::styled(bar, Style::new().fg(ACCENT)),
            dim(text),
        ])
    }

    pub(super) fn draw_downloads(&mut self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let block = panel("Downloads", self.focus == Focus::List);
        let inner = block.inner(list);
        f.render_widget(block, list);
        let rows = self.downloads_rows(inner.width as usize);
        let keys = rows.keys();
        rows.render(f, inner, &mut self.downloads_list);
        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let w = area.width as usize;
        let (lines, actions): (Vec<Line>, Vec<(&str, String)>) = match keys.get(self.downloads_list.selected().unwrap_or(0)).and_then(|i| self.jobs.get(*i)) {
            Some(j) => {
                let (label, color) = state_label(&j.state);
                let kind = match &j.source {
                    Source::Movie { .. } => "Movie",
                    Source::Episode { .. } => "Episode",
                    Source::YouTube { audio: true, .. } => "YouTube audio",
                    Source::YouTube { .. } => "YouTube video",
                };
                let mut lines = vec![Line::from(Span::styled(label.to_uppercase(), Style::new().fg(color).bold())), Line::from(j.title.clone()).bold(), Line::raw("")];
                if let Some(p) = j.progress().filter(|_| j.unfinished()) {
                    let tail = format!(" {p:.0}%");
                    let mut g = gauge(p / 100.0, w.saturating_sub(tail.len()));
                    g.push(dim(tail));
                    lines.push(Line::from(g));
                    lines.push(Line::raw(""));
                }
                lines.push(label_value("Kind", kind.into(), FG));
                let so_far = match j.real_total() {
                    Some(t) if j.unfinished() => format!("{} of {}", human_size(j.bytes), human_size(t)),
                    _ => human_size(j.bytes),
                };
                lines.push(label_value(if j.state == State::Done { "Size" } else { "So far" }, so_far, FG));
                if let Some(speed) = j.speed.filter(|_| j.state == State::Downloading) {
                    lines.push(label_value("Speed", format!("{}/s", human_size(speed as u64)), FG));
                }
                if let Some(s) = j.seconds_left().filter(|_| j.state == State::Downloading) {
                    lines.push(label_value("Time left", format!("about {}", left(s)), FG));
                }
                lines.push(label_value("Saves to", j.output.clone(), DIM));
                if j.state == State::Waiting {
                    lines.push(Line::raw(""));
                    lines.push(Line::from(dim("Paused while a recording, the restream or something you watch uses the connection. It picks up where it stopped.")));
                }
                if let Some(e) = &j.error {
                    lines.push(Line::raw(""));
                    lines.push(Line::from(Span::styled(e.clone(), Style::new().fg(BAD))));
                }
                (lines, if j.unfinished() { vec![("x", "cancel".to_string())] } else { vec![("c", "clear finished".to_string())] })
            }
            None => (
                vec![
                    Line::from("One at a time").bold(),
                    Line::raw(""),
                    Line::from(dim("Your provider allows one connection: downloads pause by themselves while a recording, the restream or something you watch runs, then resume.")),
                    Line::raw(""),
                    Line::from(dim("YouTube downloads don't use that connection and carry on.")),
                ],
                vec![],
            ),
        };
        let used = chips(f, area, &actions);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), Rect { height: area.height.saturating_sub(used), ..area });
    }

    pub(super) fn downloads_key(&mut self, key: KeyEvent) -> Option<Action> {
        let keys = self.downloads_rows(80).keys();
        if move_in(&mut self.downloads_list, key.code, keys.len()) {
            return None;
        }
        let job = keys.get(self.downloads_list.selected().unwrap_or(0)).and_then(|i| self.jobs.get(*i))?;
        match key.code {
            KeyCode::Char('c') => Some(Action::ClearFinished),
            KeyCode::Char('x') if job.unfinished() => Some(Action::CancelDownload(job.id.clone())),
            _ => None,
        }
    }
}
