//! Status: services, the connection, data freshness and folders; on the right,
//! what kd has done today (its event log).

use super::theme::*;
use super::{label_value, App, Focus};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;
use serde_json::Value;

impl App {
    pub(super) fn draw_status(&self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let s = &self.status;
        let mut lines = vec![heading("SERVICES")];
        for svc in &s.services {
            let color = match svc.state.as_str() {
                "up" => OK,
                "stopped" => BAD,
                _ => WARN,
            };
            lines.push(Line::from(vec![Span::styled("● ", Style::new().fg(color)), Span::raw(format!("{:<18}", svc.name)), dim(svc.detail.clone())]));
        }

        lines.push(Line::raw(""));
        lines.push(heading("CONNECTION"));
        if s.recording.is_empty() {
            lines.push(label_value("Recording", "nothing".into(), DIM));
        }
        for r in &s.recording {
            lines.push(label_value("Recording", r.what.clone(), BAD));
        }
        let restream = match &s.restream.channel_name {
            Some(name) if s.restream.live => (format!("{name} since {}", s.restream.since.as_deref().and_then(|t| t.get(11..16)).unwrap_or("?")), OK),
            _ => ("off".into(), DIM),
        };
        lines.push(label_value("Restream", restream.0, restream.1));
        let queued = self.jobs.iter().filter(|j| !matches!(j.state, crate::downloads::State::Done | crate::downloads::State::Failed | crate::downloads::State::Cancelled)).count();
        lines.push(label_value("Downloads", if queued == 0 { "none queued".into() } else { format!("{queued} in the queue") }, if queued == 0 { DIM } else { FG }));

        lines.push(Line::raw(""));
        lines.push(heading("DATA"));
        lines.push(label_value("Channels", age(s.list_age_minutes), freshness(s.list_age_minutes, 3 * 60)));
        lines.push(label_value("Guide", age(s.guide_age_minutes), freshness(s.guide_age_minutes, 24 * 60)));
        if let Some(c) = &s.database {
            lines.push(label_value("Database", format!("{} live · {} movies · {} series", c.live, c.vod, c.series), FG));
        }
        if let Some(a) = &s.account {
            lines.push(label_value("Account", format!("{} · expires {} · {} connection(s)", a.status, a.expires.as_deref().unwrap_or("?"), a.max_connections), FG));
        }

        lines.push(Line::raw(""));
        lines.push(heading("FOLDERS"));
        lines.push(label_value("Recordings", self.cfg.records_dir().display().to_string(), FG));
        lines.push(label_value("Downloads", self.cfg.downloads_dir().display().to_string(), FG));
        if let Some(tv) = self.cfg.tv_library_root() {
            lines.push(label_value("Jellyfin TV", tv.display().to_string(), FG));
        }

        let block = panel("Status", self.focus == Focus::List);
        let inner = block.inner(list);
        f.render_widget(block, list);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), inner);

        if !wide {
            return;
        }
        let block = panel("Today", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let events = crate::log::recent(&self.cfg, area.height as usize);
        let mut lines: Vec<Line> = events.iter().rev().map(|e| event_line(e, area.width as usize)).collect();
        if lines.is_empty() {
            lines.push(Line::from(dim("Nothing logged yet today.")));
        }
        f.render_widget(Paragraph::new(lines), area);
    }
}

/// One log event as "17:16  record.now  CA FR: NOOVO V-TELE HD"
fn event_line(e: &Value, w: usize) -> Line<'static> {
    let time = e["ts"].as_str().and_then(|t| t.get(11..16)).unwrap_or("").to_string();
    let event = e["event"].as_str().unwrap_or("").to_string();
    let what = ["channel", "title", "what", "id", "unit", "state"]
        .iter()
        .find_map(|k| e[*k].as_str().map(str::to_string))
        .unwrap_or_default();
    let color = if event.contains("fail") || event.contains("error") { BAD } else { FG };
    Line::from(vec![dim(format!("{time}  ")), Span::styled(format!("{event:<17}"), Style::new().fg(color)), dim(fit(&what, w.saturating_sub(24)))])
}

fn age(minutes: Option<i64>) -> String {
    match minutes {
        None => "never downloaded".into(),
        Some(m) if m < 60 => format!("updated {m} min ago"),
        Some(m) => format!("updated {}h{:02} ago", m / 60, m % 60),
    }
}

fn freshness(minutes: Option<i64>, stale_after: i64) -> Color {
    match minutes {
        Some(m) if m <= stale_after => FG,
        _ => WARN,
    }
}
