//! The Status tab: services, what's using the connection, data freshness.

use super::{row, section, App, BAD, DIM, OK, WARN};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

impl App {
    pub(super) fn draw_status(&self, f: &mut Frame, area: Rect) {
        let s = &self.status;
        let mut lines = vec![section("SERVICES")];
        for svc in &s.services {
            let color = match svc.state.as_str() {
                "up" => OK,
                "stopped" => BAD,
                _ => WARN,
            };
            lines.push(Line::from(vec![
                Span::styled("● ", Style::new().fg(color)),
                Span::raw(format!("{:<18}", svc.name)),
                Span::styled(svc.detail.clone(), Style::new().fg(DIM)),
            ]));
        }

        lines.push(Line::raw(""));
        lines.push(section("CONNECTION"));
        if s.recording.is_empty() {
            lines.push(row("Recording", "nothing".into(), DIM));
        }
        for r in &s.recording {
            lines.push(row("Recording", r.what.clone(), BAD));
        }
        let restream = match &s.restream.channel_name {
            Some(name) if s.restream.live => (format!("{name} since {}", s.restream.since.as_deref().unwrap_or("?")), OK),
            _ => ("off".into(), DIM),
        };
        lines.push(row("Restream", restream.0, restream.1));
        let downloading = self.jobs.iter().filter(|j| !matches!(j.state, crate::downloads::State::Done | crate::downloads::State::Failed | crate::downloads::State::Cancelled)).count();
        lines.push(row("Downloads", if downloading == 0 { "none queued".into() } else { format!("{downloading} in the queue") }, if downloading == 0 { DIM } else { Color::Reset }));

        lines.push(Line::raw(""));
        lines.push(section("DATA"));
        lines.push(row("Channels", age(s.list_age_minutes), freshness(s.list_age_minutes, 3 * 60)));
        lines.push(row("Guide", age(s.guide_age_minutes), freshness(s.guide_age_minutes, 24 * 60)));
        if let Some(c) = &s.database {
            let counts = format!("{} live · {} movies · {} series · {} guide entries", c.live, c.vod, c.series, c.guide_entries);
            lines.push(row("Database", counts, Color::Reset));
        }
        if let Some(a) = &s.account {
            let text = format!("{} · expires {} · {} connection(s)", a.status, a.expires.as_deref().unwrap_or("?"), a.max_connections);
            lines.push(row("Account", text, Color::Reset));
        }
        lines.push(Line::raw(""));
        lines.push(section("FOLDERS"));
        lines.push(row("Recordings", self.cfg.records_dir().display().to_string(), Color::Reset));
        lines.push(row("Downloads", self.cfg.downloads_dir().display().to_string(), Color::Reset));
        f.render_widget(Paragraph::new(lines), area);
    }
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
        Some(m) if m <= stale_after => Color::Reset,
        _ => WARN,
    }
}
