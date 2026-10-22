//! The menu on the left: KDTV, the nine sections in three groups, and the
//! provider's one stream in plain words at the bottom.

use super::theme::*;
use super::{App, Focus, Section, SECTIONS};
use crate::downloads::{Source, State};
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
        let icons = self.cfg.icons;
        let with_icon = |glyph: &str, text: &str| if icons { format!("{glyph}  {text}") } else { text.to_string() };

        let brand = with_icon(icon::TV, "KDTV");
        let mut lines = vec![
            Line::from(vec![
                Span::styled(brand.clone(), Style::new().fg(ACCENT).bold()),
                Span::raw(" ".repeat(width.saturating_sub(brand.chars().count() + self.cfg.profile.len()))),
                dim(self.cfg.profile.clone()),
            ]),
            Line::raw(""),
        ];
        for (i, section) in SECTIONS.iter().enumerate() {
            if i == 4 || i == 7 {
                lines.push(Line::raw(""));
            }
            let (glyph, name, count, color) = self.section_label(*section);
            let current = *section == self.section;
            let label = with_icon(glyph, name);
            let gap = width.saturating_sub(1 + label.chars().count() + count.chars().count());
            let style = if current { Style::new().bg(SELECT_BG).fg(ACCENT).bold() } else { Style::new().fg(FG) };
            lines.push(Line::from(vec![
                Span::styled(format!(" {label}{}", " ".repeat(gap)), style),
                Span::styled(count, if current { style } else { Style::new().fg(color) }),
            ]));
        }

        let (connection, color) = self.connection_words();
        let age = match self.status.list_age_minutes {
            None => "Never updated".to_string(),
            Some(m) if m < 60 => format!("Updated {m} min ago"),
            Some(m) => format!("Updated {}h ago", m / 60),
        };
        let bottom = vec![
            Line::from(Span::styled("─".repeat(width), Style::new().fg(BORDER))),
            Line::from(dim("Stream")),
            Line::from(Span::styled(format!("● {connection}"), Style::new().fg(color))),
            Line::from(dim(age)),
        ];
        let [top, end] = Layout::vertical([Constraint::Min(0), Constraint::Length(bottom.len() as u16)]).areas(inner);
        f.render_widget(Paragraph::new(lines), top);
        f.render_widget(Paragraph::new(bottom), end);
    }

    /// "available", "in use: recording"… What holds the provider's single connection.
    fn connection_words(&self) -> (String, Color) {
        let s = &self.status;
        if !s.recording.is_empty() {
            ("in use: recording".into(), BAD)
        } else if s.restream.live {
            ("in use: restreaming".into(), WARN)
        } else if self.jobs.iter().any(|j| j.state == State::Downloading && !matches!(j.source, Source::YouTube { .. })) {
            ("in use: downloading".into(), WARN)
        } else {
            ("available".into(), OK)
        }
    }

    fn section_label(&self, section: Section) -> (&'static str, &'static str, String, Color) {
        let counts = self.status.database.as_ref();
        let thousands = |n: i64| if n >= 1000 { format!("{}k", n / 1000) } else { n.to_string() };
        let unfinished = self.jobs.iter().filter(|j| j.unfinished()).count();
        match section {
            Section::Search => (icon::SEARCH, "Search", String::new(), DIM),
            Section::MyList => (icon::HEART, "My list", self.watch.saved_count().to_string(), DIM),
            Section::All => (icon::LIST, "All", counts.map(|c| thousands(c.live + c.vod + c.series)).unwrap_or_default(), DIM),
            Section::YouTube => (icon::YOUTUBE, "YouTube", String::new(), DIM),
            Section::Recordings => match self.status.recording.len() {
                0 => (icon::RECORD, "Recordings", self.schedules.iter().filter(|s| s.enabled).count().to_string(), DIM),
                n => (icon::RECORD, "Recordings", format!("● {n}"), BAD),
            },
            Section::Downloads => {
                let failed = self.jobs.iter().filter(|j| j.state == State::Failed).count();
                let (count, color) = match (unfinished, failed) {
                    (0, 0) => ("0".into(), DIM),
                    (0, n) => (format!("✗ {n}"), BAD),
                    (n, _) => (format!("↓ {n}"), ACCENT),
                };
                (icon::DOWNLOAD, "Downloads", count, color)
            }
            Section::Library => (icon::FOLDER, "Library", self.media.len().to_string(), DIM),
            Section::Account => (icon::USER, "Account", String::new(), DIM),
            Section::Status => {
                let all_up = self.status.services.iter().all(|s| s.state == "up");
                (icon::PULSE, "Status", "●".into(), if all_up { OK } else { WARN })
            }
        }
    }
}
