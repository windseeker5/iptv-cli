//! Pop-ups over the current tab: a menu, a yes/no question, or a short form.

use super::hhmm;
use super::theme::*;
use crate::db::{Channel, Db, Episode, Movie, Program};
use crate::library::Media;
use crate::youtube::Video;
use crate::schedules;
use ratatui::crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Flex, Layout, Rect};
use ratatui::style::{Color, Style, Stylize};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, List, ListItem, ListState, Padding, Paragraph, Wrap};
use ratatui::Frame;

/// Everything the user can ask for from the TUI.
#[derive(Clone)]
pub enum Action {
    PlayLive(Channel),
    PlayMovie(Movie),
    PlayEpisode { series: String, episode: Episode },
    PlayFile(Media),
    PlayYouTube(Video),
    RecordMenu(Channel),
    RecordForm(Channel),
    ScheduleForm(Channel),
    RecordNow { channel: Channel, minutes: i64, show: Option<String> },
    Schedule { channel: Channel, time: String, minutes: i64, days: Vec<String>, date: Option<String>, show: Option<String> },
    StopRecording(String),
    PauseSchedule { id: String, enabled: bool },
    DeleteSchedule(String),
    RestreamStart { channel: Channel, replace: bool },
    RestreamStop,
    DownloadMovie(Movie),
    /// `what` names the batch for the question: "season 3", "all 12 seasons".
    DownloadEpisodes { series: String, episodes: Vec<Episode>, what: String },
    DownloadYouTube { video: Video, audio: bool },
    CancelDownload(String),
    Refresh { guide: bool, everything: bool },
}

pub enum Modal {
    Menu { title: String, items: Vec<(String, Action)>, state: ListState },
    Confirm { text: String, action: Action },
    Form(Form),
    /// Something to read (an error, the keys); any key closes it.
    Message { title: String, lines: Vec<Line<'static>>, color: Color },
}

/// A one-time schedule for a show in the guide (a few minutes of margin at the end).
pub fn schedule_action(channel: &Channel, p: &Program) -> Action {
    let start = chrono::DateTime::from_timestamp(p.start_ts, 0).map(|t| t.with_timezone(&chrono::Local)).unwrap_or_else(chrono::Local::now);
    let minutes = ((p.end_ts - p.start_ts) / 60 + 3).clamp(crate::record::MIN_MINUTES, crate::record::MAX_MINUTES);
    Action::Schedule {
        channel: channel.clone(),
        time: start.format("%H:%M").to_string(),
        minutes,
        days: vec![],
        date: Some(start.format("%Y-%m-%d").to_string()),
        show: None,
    }
}

pub struct Form {
    title: String,
    channel: Channel,
    kind: FormKind,
    fields: Vec<Field>,
    focus: usize,
    error: Option<String>,
}

#[derive(PartialEq)]
enum FormKind {
    RecordNow,
    Schedule,
}

struct Field {
    label: &'static str,
    value: String,
    hint: &'static str,
}

pub enum Outcome {
    Keep(Modal),
    Close,
    /// Run the action; `true` means the user already confirmed it.
    Run(Action, bool),
}

impl Modal {
    pub fn confirm(text: String, action: Action) -> Modal {
        Modal::Confirm { text, action }
    }

    pub fn message(title: &str, text: String, color: Color) -> Modal {
        Modal::Message { title: title.into(), lines: vec![Line::from(Span::styled(text, Style::new().fg(color))), Line::raw(""), Line::from(dim("any key to close"))], color }
    }

    pub fn help() -> Modal {
        let key = |k: &str, what: &str| Line::from(vec![Span::styled(format!("{k:<12}"), Style::new().fg(ACCENT).bold()), Span::raw(what.to_string())]);
        let lines = vec![
            heading("EVERYWHERE"),
            key("1 … 9, 0", "jump to a section (Search is 1, Status is 0)"),
            key("/", "search"),
            key("tab", "switch between the menu and the list"),
            key("↑ ↓  → ←", "move · open · go back"),
            key("q", "quit (recordings, restream and downloads keep going)"),
            Line::raw(""),
            heading("LIVE"),
            key("↵  r  n", "watch · record · schedule the next show"),
            key("s  x", "restream for the family · stop the restream"),
            Line::raw(""),
            heading("MOVIES · SERIES · YOUTUBE"),
            key("↵  d", "watch · download"),
            key("[  ]", "previous / next season"),
            key("s  D", "download this season · every season"),
            key("a", "YouTube: download the audio only (mp3)"),
            Line::raw(""),
            heading("RECORDINGS · DOWNLOADS · ACCOUNT"),
            key("x", "stop a recording · cancel a download"),
            key("space  d", "pause or resume a schedule · delete it"),
            key("u  g", "update the channel list · the TV guide"),
            Line::raw(""),
            Line::from(dim("any key to close")),
        ];
        Modal::Message { title: "Keys".into(), lines, color: ACCENT }
    }

    /// `r` on a live channel: this show, the next ones, or a custom time.
    pub fn record_menu(channel: &Channel, guide: &[Program], schedules: &[crate::schedules::Schedule]) -> Modal {
        let now = chrono::Utc::now().timestamp();
        let mut items = vec![];
        for p in guide.iter().take(4) {
            let title = p.title.clone().unwrap_or_else(|| "this show".into());
            let minutes = ((p.end_ts - p.start_ts.max(now)) / 60 + 3).clamp(crate::record::MIN_MINUTES, crate::record::MAX_MINUTES);
            if p.start_ts <= now {
                items.push((
                    format!("Record “{title}” now, until {} ({minutes} min)", hhmm(p.end_ts)),
                    Action::RecordNow { channel: channel.clone(), minutes, show: None },
                ));
            } else if super::watch::will_record(schedules, channel.stream_id, p.start_ts) {
                // Already scheduled: offering it again would only make a clashing copy.
                continue;
            } else {
                let day = chrono::DateTime::from_timestamp(p.start_ts, 0)
                    .map(|t| t.with_timezone(&chrono::Local).format("%a").to_string())
                    .unwrap_or_default();
                items.push((format!("Schedule “{title}” {day} {}–{}", hhmm(p.start_ts), hhmm(p.end_ts)), schedule_action(channel, p)));
            }
        }
        items.push(("Record now for… (choose how long)".into(), Action::RecordForm(channel.clone())));
        items.push(("Schedule… (time, repeat days, Jellyfin show)".into(), Action::ScheduleForm(channel.clone())));
        Modal::Menu { title: format!("Record {}", channel.name), items, state: ListState::default().with_selected(Some(0)) }
    }

    pub fn record_form(channel: Channel, db: &Db) -> Modal {
        let minutes = crate::minutes_until_show_ends(db, channel.stream_id, chrono::Utc::now().timestamp()).unwrap_or(60);
        Modal::Form(Form {
            title: format!("Record {} now", channel.name),
            channel,
            kind: FormKind::RecordNow,
            fields: vec![
                Field { label: "Minutes", value: minutes.to_string(), hint: "up to 360" },
                Field { label: "Jellyfin show", value: String::new(), hint: "optional: file it in Jellyfin under this show" },
            ],
            focus: 0,
            error: None,
        })
    }

    pub fn schedule_form(channel: Channel) -> Modal {
        Modal::Form(Form {
            title: format!("Schedule {}", channel.name),
            channel,
            kind: FormKind::Schedule,
            fields: vec![
                Field { label: "Time", value: String::new(), hint: "like 18:29" },
                Field { label: "Minutes", value: "60".into(), hint: "up to 360" },
                Field { label: "Repeat", value: String::new(), hint: "Mon,Wed · weekdays · weekend · daily · empty = once" },
                Field { label: "Date", value: String::new(), hint: "once only: YYYY-MM-DD, empty = next time it comes" },
                Field { label: "Jellyfin show", value: String::new(), hint: "optional: file it in Jellyfin under this show" },
            ],
            focus: 0,
            error: None,
        })
    }

    pub fn on_key(self, key: KeyEvent) -> Outcome {
        match self {
            Modal::Message { .. } => Outcome::Close,
            Modal::Confirm { text, action } => match key.code {
                KeyCode::Enter | KeyCode::Char('y') => Outcome::Run(action, true),
                KeyCode::Esc | KeyCode::Char('n') | KeyCode::Char('q') => Outcome::Close,
                _ => Outcome::Keep(Modal::Confirm { text, action }),
            },
            Modal::Menu { title, items, mut state } => {
                let chosen = match key.code {
                    KeyCode::Esc | KeyCode::Char('q') => return Outcome::Close,
                    KeyCode::Enter => state.selected(),
                    KeyCode::Char(c @ '1'..='9') => Some(c as usize - '1' as usize).filter(|i| *i < items.len()),
                    code => {
                        super::move_in(&mut state, code, items.len());
                        None
                    }
                };
                match chosen.and_then(|i| items.get(i)) {
                    Some((_, action)) => Outcome::Run(action.clone(), false),
                    None => Outcome::Keep(Modal::Menu { title, items, state }),
                }
            }
            Modal::Form(mut form) => match key.code {
                KeyCode::Esc => Outcome::Close,
                KeyCode::Tab | KeyCode::Down => {
                    form.focus = (form.focus + 1) % form.fields.len();
                    Outcome::Keep(Modal::Form(form))
                }
                KeyCode::BackTab | KeyCode::Up => {
                    form.focus = (form.focus + form.fields.len() - 1) % form.fields.len();
                    Outcome::Keep(Modal::Form(form))
                }
                KeyCode::Backspace => {
                    form.fields[form.focus].value.pop();
                    Outcome::Keep(Modal::Form(form))
                }
                KeyCode::Char(c) => {
                    form.fields[form.focus].value.push(c);
                    form.error = None;
                    Outcome::Keep(Modal::Form(form))
                }
                KeyCode::Enter => match form.submit() {
                    Ok(action) => Outcome::Run(action, false),
                    Err(e) => {
                        form.error = Some(e);
                        Outcome::Keep(Modal::Form(form))
                    }
                },
                _ => Outcome::Keep(Modal::Form(form)),
            },
        }
    }

    pub fn draw(&mut self, f: &mut Frame, area: Rect) {
        let border = match self {
            Modal::Message { color, .. } => *color,
            _ => ACCENT,
        };
        type MenuParts<'m> = Option<(&'m Vec<(String, Action)>, &'m mut ListState)>;
        let (title, lines, list): (String, Vec<Line>, MenuParts) = match self {
            Modal::Confirm { text, .. } => (
                "Please confirm".into(),
                vec![Line::raw(text.clone()), Line::raw(""), Line::from("↵ yes   esc no").fg(DIM)],
                None,
            ),
            Modal::Menu { title, items, state } => (title.clone(), vec![], Some((items, state))),
            Modal::Form(form) => (form.title.clone(), form.lines(), None),
            Modal::Message { title, lines, .. } => (title.clone(), lines.clone(), None),
        };
        let width = area.width.saturating_sub(4).min(80);
        let text_rows: u16 = lines.iter().map(|l| (l.width() as u16 / width.saturating_sub(4).max(1)) + 1).sum();
        let height = match &list {
            Some((items, _)) => items.len() as u16 + 2,
            None => text_rows + 2,
        };
        let [v] = Layout::vertical([Constraint::Length(height.min(area.height))]).flex(Flex::Center).areas(area);
        let [rect] = Layout::horizontal([Constraint::Length(width)]).flex(Flex::Center).areas(v);
        f.render_widget(Clear, rect);
        let block = Block::bordered()
            .border_type(BorderType::Rounded)
            .border_style(Style::new().fg(border))
            .style(Style::new().bg(BG).fg(FG))
            .title(Line::from(format!(" {title} ")).bold())
            .padding(Padding::horizontal(1));
        match list {
            Some((items, state)) => {
                let rows: Vec<ListItem> =
                    items.iter().enumerate().map(|(i, (label, _))| ListItem::new(format!("{}  {label}", i + 1))).collect();
                let widget = List::new(rows).block(block).highlight_style(Style::new().bg(SELECT_BG).fg(ACCENT).bold());
                f.render_stateful_widget(widget, rect, state);
            }
            None => f.render_widget(Paragraph::new(lines).block(block).wrap(Wrap { trim: false }), rect),
        }
    }
}

impl Form {
    fn lines(&self) -> Vec<Line<'static>> {
        let mut lines = vec![];
        for (i, field) in self.fields.iter().enumerate() {
            let focused = i == self.focus;
            let label = Span::styled(format!("{:<14}", field.label), Style::new().fg(if focused { ACCENT } else { DIM }));
            let mut value = vec![label, Span::raw(field.value.clone())];
            if focused {
                value.push(Span::styled("▏", Style::new().fg(ACCENT)));
            }
            lines.push(Line::from(value));
            lines.push(Line::from(format!("{:<14}{}", "", field.hint)).fg(DIM));
        }
        if let Some(e) = &self.error {
            lines.push(Line::from(e.clone()).fg(BAD));
        }
        lines.push(Line::from("↵ save   tab next field   esc cancel").fg(DIM));
        lines
    }

    fn value(&self, label: &str) -> String {
        self.fields.iter().find(|f| f.label == label).map(|f| f.value.trim().to_string()).unwrap_or_default()
    }

    fn submit(&self) -> Result<Action, String> {
        let minutes: i64 = self.value("Minutes").parse().map_err(|_| "Minutes must be a number".to_string())?;
        let show = Some(self.value("Jellyfin show")).filter(|s| !s.is_empty());
        if self.kind == FormKind::RecordNow {
            if !(crate::record::MIN_MINUTES..=crate::record::MAX_MINUTES).contains(&minutes) {
                return Err(format!("Minutes must be between {} and {}", crate::record::MIN_MINUTES, crate::record::MAX_MINUTES));
            }
            return Ok(Action::RecordNow { channel: self.channel.clone(), minutes, show });
        }
        let time = self.value("Time");
        let (days, day_error) = match parse_days(&self.value("Repeat")) {
            Ok(days) => (days, None),
            Err(e) => (vec!["Mon".to_string()], Some(e)),
        };
        let date = match (days.is_empty(), self.value("Date")) {
            (false, _) => None,
            (true, d) if d.is_empty() => Some(crate::next_date_for(&time)),
            (true, d) => Some(d),
        };
        let new = schedules::New {
            stream_id: self.channel.stream_id,
            channel_name: &self.channel.name,
            time: &time,
            duration_min: minutes,
            days: days.clone(),
            date: date.clone(),
            show_name: show.as_deref(),
            notify: true,
            created_by: "kd",
        };
        let errors: Vec<String> = day_error.into_iter().chain(schedules::validate(&new)).collect();
        if !errors.is_empty() {
            return Err(errors.join("; "));
        }
        Ok(Action::Schedule { channel: self.channel.clone(), time, minutes, days, date, show })
    }
}

/// "mon, wed" → ["Mon", "Wed"]; also "weekdays", "weekend", "daily".
fn parse_days(text: &str) -> Result<Vec<String>, String> {
    let all = schedules::DAY_NAMES;
    let mut days = vec![];
    for word in text.split([',', ' ', '/']).map(str::trim).filter(|w| !w.is_empty()) {
        match word.to_lowercase().as_str() {
            "daily" | "everyday" | "every day" => days.extend(all),
            "weekdays" => days.extend(&all[..5]),
            "weekend" | "weekends" => days.extend(&all[5..]),
            w => {
                let day = all.iter().find(|d| w.starts_with(&d.to_lowercase())).ok_or(format!("I don't know the day “{word}”"))?;
                days.push(day);
            }
        }
    }
    Ok(all.iter().filter(|d| days.contains(d)).map(|d| d.to_string()).collect())
}

#[cfg(test)]
mod tests {
    use super::parse_days;

    #[test]
    fn days_in_plain_words() {
        assert_eq!(parse_days("").unwrap(), Vec::<String>::new());
        assert_eq!(parse_days("wed, mon").unwrap(), ["Mon", "Wed"]);
        assert_eq!(parse_days("weekdays").unwrap(), ["Mon", "Tue", "Wed", "Thu", "Fri"]);
        assert_eq!(parse_days("Sunday").unwrap(), ["Sun"]);
        assert!(parse_days("someday").is_err());
    }
}
