//! The first screen on a new computer (and `e` in Account on Windows): the
//! provider login, checked right away, then the first download of the channels,
//! movies, series and guide. The login goes into kdtv's `.env` and nowhere else.

use super::theme::*;
use crate::config::Config;
use anyhow::Result;
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::layout::{Constraint, Flex, Layout};
use ratatui::style::{Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, Padding, Paragraph, Wrap};
use ratatui::{DefaultTerminal, Frame};
use std::sync::mpsc::{channel, Receiver};
use std::time::{Duration, Instant};

/// No login yet, or no channel list downloaded with it.
pub fn needed(cfg: &Config) -> bool {
    let p = &cfg.provider;
    p.server.is_empty() || p.username.is_empty() || p.password.is_empty() || !cfg.data_dir.join("iptv.db").exists()
}

struct Field {
    label: &'static str,
    hint: &'static str,
    value: String,
    secret: bool,
}

enum Step {
    Working(String),
    Done(Result<Box<Config>, String>),
}

struct Setup {
    fields: Vec<Field>,
    focus: usize,
    error: Option<String>,
    working: Option<(String, Receiver<Step>)>,
    started: Instant,
    /// Changing the login of a computer that already works (Esc goes back).
    change: bool,
}

/// Ask for the login until it works. `Some(config)` once the lists are downloaded,
/// `None` when the user leaves with Esc.
pub fn run(terminal: &mut DefaultTerminal, cfg: &Config, change: bool) -> Result<Option<Config>> {
    let p = &cfg.provider;
    let mut setup = Setup {
        fields: vec![
            Field { label: "Server", hint: "from your provider, like http://line.example.com:8080", value: p.server.clone(), secret: false },
            Field { label: "Username", hint: "", value: p.username.clone(), secret: false },
            Field { label: "Password", hint: "", value: p.password.clone(), secret: true },
        ],
        focus: 0,
        error: None,
        working: None,
        started: Instant::now(),
        change,
    };
    setup.focus = setup.fields.iter().position(|f| f.value.is_empty()).unwrap_or(0);
    loop {
        terminal.draw(|f| setup.draw(f))?;
        if let Some((_, rx)) = &setup.working {
            match rx.try_recv() {
                Ok(Step::Working(text)) => setup.working = setup.working.take().map(|(_, rx)| (text, rx)),
                Ok(Step::Done(Ok(cfg))) => return Ok(Some(*cfg)),
                Ok(Step::Done(Err(e))) => {
                    setup.working = None;
                    setup.error = Some(e);
                }
                Err(_) => {}
            }
        }
        if !event::poll(Duration::from_millis(150))? {
            continue;
        }
        let ev = event::read()?;
        if setup.working.is_some() {
            continue; // nothing to type while it connects
        }
        match ev {
            Event::Paste(text) => setup.type_text(text.trim()),
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
                match key.code {
                    KeyCode::Char('c') if ctrl => return Ok(None),
                    KeyCode::Esc => return Ok(None),
                    KeyCode::Tab | KeyCode::Down => setup.focus = (setup.focus + 1) % setup.fields.len(),
                    KeyCode::BackTab | KeyCode::Up => setup.focus = (setup.focus + setup.fields.len() - 1) % setup.fields.len(),
                    KeyCode::Backspace => {
                        setup.fields[setup.focus].value.pop();
                    }
                    KeyCode::Char(c) => setup.type_text(&c.to_string()),
                    KeyCode::Enter => match setup.fields.iter().position(|f| f.value.trim().is_empty()) {
                        Some(empty) => setup.focus = empty,
                        None => setup.connect(cfg),
                    },
                    _ => {}
                }
            }
            _ => {}
        }
    }
}

impl Setup {
    fn type_text(&mut self, text: &str) {
        self.fields[self.focus].value.push_str(text);
        self.error = None;
    }

    /// Save the login, check it with the provider and download the lists, in the background.
    fn connect(&mut self, cfg: &Config) {
        let mut server = self.fields[0].value.trim().trim_end_matches('/').to_string();
        if !server.contains("://") {
            server = format!("http://{server}");
        }
        self.fields[0].value = server.clone();
        let (user, password) = (self.fields[1].value.trim().to_string(), self.fields[2].value.trim().to_string());
        let cfg = cfg.clone();
        let (tx, rx) = channel();
        self.working = Some(("Checking your login…".into(), rx));
        self.started = Instant::now();
        self.error = None;
        std::thread::spawn(move || {
            let result = (|| -> Result<Box<Config>, String> {
                crate::config::save_login(&cfg.env_file, &server, &user, &password).map_err(|e| format!("{e:#}"))?;
                let new = Config::load(cfg.file.as_deref()).map_err(|e| format!("{e:#}"))?;
                crate::refresh::check_login(&new).map_err(|e| new.provider.redact(&format!("{e:#}")))?;
                let _ = tx.send(Step::Working("Login OK. Downloading your channels, movies, series and TV guide: about a minute…".into()));
                std::fs::create_dir_all(&new.data_dir).map_err(|e| e.to_string())?;
                crate::refresh::run(&new, crate::refresh::What { guide: true, everything: true }).map_err(|e| new.provider.redact(&format!("{e:#}")))?;
                crate::log::event(&new, "setup.done", serde_json::json!({}));
                Ok(Box::new(new))
            })();
            let _ = tx.send(Step::Done(result));
        });
    }

    fn draw(&self, f: &mut Frame) {
        f.render_widget(Block::new().style(Style::new().bg(BG).fg(FG)), f.area());
        let mut lines = vec![
            Line::from(Span::styled(if self.change { "Change your provider login" } else { "Welcome to kdtv" }, Style::new().fg(ACCENT).bold())),
            Line::raw(""),
            Line::raw("Type the login your IPTV provider gave you."),
            Line::from(dim("It stays on this computer, in kdtv's own folder.")),
            Line::raw(""),
        ];
        for (i, field) in self.fields.iter().enumerate() {
            let focused = i == self.focus && self.working.is_none();
            let shown = if field.secret { "•".repeat(field.value.chars().count()) } else { field.value.clone() };
            let mut row = vec![Span::styled(format!("{:<12}", field.label), Style::new().fg(if focused { ACCENT } else { DIM })), Span::raw(shown)];
            if focused {
                row.push(Span::styled("▏", Style::new().fg(ACCENT)));
            }
            lines.push(Line::from(row));
            if !field.hint.is_empty() {
                lines.push(Line::from(dim(format!("{:<12}{}", "", field.hint))));
            }
            lines.push(Line::raw(""));
        }
        match (&self.working, &self.error) {
            (Some((text, _)), _) => {
                let spin = ["◐", "◓", "◑", "◒"][(self.started.elapsed().as_millis() / 200 % 4) as usize];
                lines.push(Line::from(vec![Span::styled(format!("{spin} "), Style::new().fg(ACCENT)), Span::raw(text.clone())]));
                lines.push(Line::from(dim(format!("{} s", self.started.elapsed().as_secs()))));
            }
            (None, Some(e)) => {
                lines.push(Line::from(Span::styled(format!("That didn't work: {e}"), Style::new().fg(BAD))));
                lines.push(Line::from(dim("Check the three lines above and press ↵ to try again.")));
            }
            (None, None) => lines.push(Line::from(dim(if self.change {
                "↵ connect   tab next line   esc keep the old login"
            } else {
                "↵ connect   tab next line   esc quit"
            }))),
        }

        let area = f.area();
        let width = area.width.saturating_sub(4).min(84);
        // Rows once wrapped to the box (2 for the border, 4 for the padding).
        let inner = width.saturating_sub(6).max(1) as usize;
        let rows: usize = lines.iter().map(|l| l.width().max(1).div_ceil(inner)).sum();
        let height = (rows as u16 + 4).min(area.height);
        let [v] = Layout::vertical([Constraint::Length(height)]).flex(Flex::Center).areas(area);
        let [rect] = Layout::horizontal([Constraint::Length(width)]).flex(Flex::Center).areas(v);
        f.render_widget(Clear, rect);
        let block = Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(Style::new().fg(ACCENT))
            .style(Style::new().bg(BG).fg(FG))
            .title(Line::from(" kdtv ").bold())
            .padding(Padding::new(2, 2, 1, 1));
        f.render_widget(Paragraph::new(lines).block(block).wrap(Wrap { trim: false }), rect);
    }
}
