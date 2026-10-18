//! Account: where the provider login lives, the subscription, and the lists
//! with "update now" buttons.

use super::modal::Action;
use super::theme::*;
use super::{label_value, App, Focus};
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::Rect;
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

fn age(minutes: Option<i64>) -> String {
    match minutes {
        None => "never".into(),
        Some(m) if m < 60 => format!("{m} min ago"),
        Some(m) if m < 48 * 60 => format!("{}h{:02} ago", m / 60, m % 60),
        Some(m) => format!("{} days ago", m / 1440),
    }
}

fn thousands(n: i64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(c);
    }
    out
}

impl App {
    pub(super) fn draw_account(&self, f: &mut Frame, list: Rect, detail: Rect, wide: bool) {
        let p = &self.cfg.provider;
        let s = &self.status;
        let missing = |v: &str| if v.is_empty() { "not set".to_string() } else { v.to_string() };
        let password = if p.password.is_empty() {
            "not set".to_string()
        } else if self.show_password {
            p.password.clone()
        } else {
            "•".repeat(10)
        };
        let button = |key: &str, label: &str| Line::from(vec![Span::raw(" ".repeat(16)), Span::styled(format!(" {key} "), Style::new().fg(BG).bg(ACCENT).bold()), Span::raw(format!(" {label}"))]);

        let mut lines = vec![heading("YOUR PROVIDER")];
        lines.push(label_value("Server", missing(&p.server), FG));
        lines.push(label_value("User name", missing(&p.username), FG));
        lines.push(Line::from(vec![Span::styled(format!("{:<16}", "Password"), Style::new().fg(DIM)), Span::raw(password), dim(if self.show_password { "   p to hide" } else { "   p to show" })]));
        if let Some(a) = &s.account {
            let color: Color = if a.status.eq_ignore_ascii_case("active") { OK } else { BAD };
            lines.push(label_value("Subscription", format!("{} · until {}", a.status, a.expires.as_deref().unwrap_or("?")), color));
            lines.push(label_value("Connections", format!("{} at a time", a.max_connections), FG));
        }

        lines.push(Line::raw(""));
        lines.push(heading("YOUR LISTS"));
        let counts = s.database.as_ref();
        lines.push(label_value("Live channels", format!("{} · updated {}", counts.map(|c| thousands(c.live)).unwrap_or_default(), age(s.list_age_minutes)), FG));
        lines.push(button("u", "update the channel list now (a few seconds)"));
        lines.push(label_value("TV guide", format!("{} shows · updated {}", counts.map(|c| thousands(c.guide_entries)).unwrap_or_default(), age(s.guide_age_minutes)), FG));
        lines.push(button("g", "update the TV guide too (about 30 seconds)"));
        if let Some(c) = counts {
            lines.push(label_value("Movies, series", format!("{} · {}", thousands(c.vod), thousands(c.series)), FG));
        }
        lines.push(button("m", "update everything: also movies and series (about a minute)"));
        lines.push(Line::from(dim("They also update by themselves: channels every hour, the guide twice a day.")));

        lines.push(Line::raw(""));
        lines.push(heading("STORED IN"));
        lines.push(label_value("Login file", self.cfg.env_file.display().to_string(), FG));
        lines.push(button("e", "edit it (opens your editor, then comes back)"));

        let block = panel("Account", self.focus == Focus::List);
        let inner = block.inner(list);
        f.render_widget(block, list);
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), inner);

        if !wide {
            return;
        }
        let block = panel("", false);
        let area = block.inner(detail);
        f.render_widget(block, detail);
        let lines = vec![
            Line::from("Missing a channel?").bold(),
            Line::from(dim("Game channels appear a few hours before a game. Press u to get the newest list.")),
            Line::raw(""),
            Line::from("Changed provider?").bold(),
            Line::from(dim("Press e and change IPTV_SERVER_URL, IPTV_USERNAME and IPTV_PASSWORD.")),
            Line::raw(""),
            Line::from(Span::styled("─".repeat(area.width as usize), Style::new().fg(BORDER))),
            Line::from(dim("The login is never written to logs or messages, and JP never sees it.")),
        ];
        f.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), area);
    }

    pub(super) fn account_key(&mut self, key: KeyEvent) -> Option<Action> {
        match key.code {
            KeyCode::Char('u') => Some(Action::Refresh { guide: false, everything: false }),
            KeyCode::Char('g') => Some(Action::Refresh { guide: true, everything: false }),
            KeyCode::Char('m') => Some(Action::Refresh { guide: true, everything: true }),
            KeyCode::Char('p') => {
                self.show_password = !self.show_password;
                None
            }
            KeyCode::Char('e') => {
                self.edit_env = true;
                None
            }
            _ => None,
        }
    }
}
