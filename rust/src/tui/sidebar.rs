//! The left panel: the six sections with their counts, and the connection at the bottom.

use super::theme::*;
use super::{App, Focus, Section, SECTIONS};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

impl App {
    pub(super) fn draw_sidebar(&self, f: &mut Frame, area: Rect) {
        let block = panel("", self.focus == Focus::Sidebar);
        let inner = block.inner(area);
        f.render_widget(block, area);
        let width = inner.width as usize;

        let mut lines = vec![
            Line::from(vec![
                Span::styled("kd", Style::new().fg(ACCENT).bold()),
                Span::raw(" ".repeat(width.saturating_sub(2 + self.cfg.profile.len()))),
                dim(self.cfg.profile.clone()),
            ]),
            Line::raw(""),
        ];
        for (i, section) in SECTIONS.iter().enumerate() {
            if i == 3 {
                lines.push(Line::raw(""));
            }
            let (name, count, color) = self.section_label(*section);
            let current = *section == self.section;
            let mark = if current { "▸ " } else { "  " };
            let gap = width.saturating_sub(mark.len() + name.len() + count.chars().count());
            let style = if current { Style::new().bg(SELECT_BG).fg(ACCENT).bold() } else { Style::new().fg(FG) };
            lines.push(Line::from(vec![
                Span::styled(format!("{mark}{name}{}", " ".repeat(gap)), style),
                Span::styled(count, if current { style } else { Style::new().fg(color) }),
            ]));
        }

        let s = &self.status;
        let max = s.account.as_ref().map_or(1, |a| a.max_connections.max(1));
        let used = s.recording.len() as i64 + i64::from(s.restream.live);
        let mut bottom = vec![Line::from(dim("Connection"))];
        bottom.push(if used >= max {
            Line::from(Span::styled(format!("▮ {used} of {max} in use"), Style::new().fg(WARN)))
        } else {
            Line::from(Span::styled(format!("▯ free ({used} of {max})"), Style::new().fg(OK)))
        });
        let age = |m: Option<i64>| match m {
            None => "never".to_string(),
            Some(m) if m < 60 => format!("{m} min old"),
            Some(m) => format!("{}h old", m / 60),
        };
        bottom.push(Line::from(dim(format!("Guide {}", age(s.guide_age_minutes)))));

        let [top, end] = Layout::vertical([Constraint::Min(0), Constraint::Length(bottom.len() as u16)]).areas(inner);
        f.render_widget(Paragraph::new(lines), top);
        f.render_widget(Paragraph::new(bottom), end);
    }

    fn section_label(&self, section: Section) -> (&'static str, String, Color) {
        let counts = self.status.database.as_ref();
        let thousands = |n: i64| if n >= 1000 { format!("{}k", n / 1000) } else { n.to_string() };
        match section {
            Section::Live => ("Live", self.favorites.to_string(), DIM),
            Section::Movies => ("Movies", counts.map(|c| thousands(c.vod)).unwrap_or_default(), DIM),
            Section::Series => ("Series", counts.map(|c| thousands(c.series)).unwrap_or_default(), DIM),
            Section::Record => match self.status.recording.len() {
                0 => ("Record", self.schedules.iter().filter(|s| s.enabled).count().to_string(), DIM),
                n => ("Record", format!("● {n}"), BAD),
            },
            Section::Library => ("Library", self.media.len().to_string(), DIM),
            Section::Status => {
                let all_up = self.status.services.iter().all(|s| s.state == "up");
                ("Status", "●".into(), if all_up { OK } else { WARN })
            }
        }
    }
}
