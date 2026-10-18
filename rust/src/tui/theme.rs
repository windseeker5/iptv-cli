//! The look (design option B): dark ground, one teal accent, rounded panels,
//! orange for "busy", red for recording.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Padding, Paragraph};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

pub const BG: Color = Color::Rgb(13, 17, 23);
pub const FG: Color = Color::Rgb(230, 237, 243);
pub const DIM: Color = Color::Rgb(139, 148, 158);
pub const BORDER: Color = Color::Rgb(48, 54, 61);
pub const ACCENT: Color = Color::Rgb(94, 234, 212);
pub const SELECT_BG: Color = Color::Rgb(28, 42, 42);
pub const OK: Color = Color::Rgb(63, 185, 80);
pub const WARN: Color = Color::Rgb(255, 166, 87);
pub const BAD: Color = Color::Rgb(255, 123, 114);

/// A rounded panel; the focused one has the accent border.
pub fn panel(title: &str, focused: bool) -> Block<'static> {
    let block = Block::bordered()
        .border_type(BorderType::Rounded)
        .border_style(Style::new().fg(if focused { ACCENT } else { BORDER }))
        .padding(Padding::horizontal(1))
        .style(Style::new().bg(BG).fg(FG));
    if title.is_empty() { block } else { block.title(Line::from(format!(" {title} ")).bold().fg(FG)) }
}

/// Small grey capitals over a group of rows.
pub fn heading(text: &str) -> Line<'static> {
    Line::from(text.to_string()).fg(DIM).add_modifier(Modifier::BOLD)
}

pub fn dim(text: impl Into<String>) -> Span<'static> {
    Span::styled(text.into(), Style::new().fg(DIM))
}

/// Exactly `width` columns: cut with "…" or padded with spaces.
pub fn fit(text: &str, width: usize) -> String {
    if text.width() <= width {
        return format!("{text}{}", " ".repeat(width - text.width()));
    }
    let mut out = String::new();
    for c in text.chars() {
        if out.width() + unicode_width::UnicodeWidthChar::width(c).unwrap_or(0) + 1 > width {
            break;
        }
        out.push(c);
    }
    let mut out = out.trim_end().to_string();
    out.push('…');
    format!("{out}{}", " ".repeat(width.saturating_sub(out.width())))
}

/// "▰▰▰▱▱": how much of a show has gone by.
pub fn blocks(done: f64, cells: usize) -> String {
    let filled = ((done.clamp(0.0, 1.0)) * cells as f64).round() as usize;
    format!("{}{}", "▰".repeat(filled), "▱".repeat(cells - filled))
}

/// A thin full-width progress line: accent for the elapsed part.
pub fn gauge(done: f64, width: usize) -> Vec<Span<'static>> {
    let filled = ((done.clamp(0.0, 1.0)) * width as f64).round() as usize;
    vec![
        Span::styled("━".repeat(filled), Style::new().fg(ACCENT)),
        Span::styled("━".repeat(width.saturating_sub(filled)), Style::new().fg(BORDER)),
    ]
}

/// "1h31" / "12 min"
pub fn left(seconds: i64) -> String {
    let m = (seconds.max(0) + 59) / 60;
    if m >= 60 { format!("{}h{:02}", m / 60, m % 60) } else { format!("{m} min") }
}

/// The action buttons at the bottom of a detail panel: two per row, boxed.
pub fn chips(f: &mut Frame, area: Rect, chips: &[(&str, &str)]) -> u16 {
    let rows = chips.len().div_ceil(2) as u16;
    let height = rows * 3;
    if area.height < height || chips.is_empty() {
        return 0;
    }
    let top = area.y + area.height - height;
    let half = area.width / 2;
    for (i, (key, label)) in chips.iter().enumerate() {
        let rect = Rect {
            x: area.x + if i % 2 == 0 { 0 } else { half + 1 },
            y: top + (i / 2) as u16 * 3,
            width: if i % 2 == 0 { half } else { area.width - half - 1 },
            height: 3,
        };
        let line = Line::from(vec![Span::styled(key.to_string(), Style::new().fg(ACCENT).bold()), Span::raw(format!(" {label}"))]);
        let block = Block::bordered().border_type(BorderType::Rounded).border_style(Style::new().fg(BORDER));
        f.render_widget(Paragraph::new(line).block(block), rect);
    }
    height
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fitting_text_into_columns() {
        assert_eq!(fit("NOOVO", 8), "NOOVO   ");
        assert_eq!(fit("Passions tourmentées", 10), "Passions… ");
        assert_eq!(fit("Passions tourmentées", 10).width(), 10);
        assert_eq!(blocks(0.5, 4), "▰▰▱▱");
        assert_eq!(left(91 * 60), "1h31");
        assert_eq!(left(12 * 60 - 30), "12 min");
    }
}
