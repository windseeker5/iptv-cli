"""Minimal input screen for scheduling a live recording."""

import math
from datetime import datetime

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Label

from iptv_tui.domain import actions, iptv_provider, recordings
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class ScheduleRecordingScreen(Screen):
    """Collect a start time and duration, then schedule via systemd."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Record — {self.item.get('name', '')}")
        yield StatusBar("Choose when to start and how many minutes to record")
        yield Label("Start (now, 18:29, 1829, 629, 6:29pm, or tomorrow 20:00):")
        yield Input(value="now", id="start-input")
        yield Label("Duration (minutes, or 'program' to record until the show ends):")
        yield Input(value="30", id="duration-input")

    def on_mount(self) -> None:
        self.query_one("#start-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "start-input":
            self.query_one("#duration-input", Input).focus()
            return
        self._submit()

    def _submit(self) -> None:
        start_input = self.query_one("#start-input", Input).value.strip()
        duration_input = self.query_one("#duration-input", Input).value.strip()
        status = self.query_one(StatusBar)

        try:
            start_time = recordings.parse_start_time(start_input)
        except ValueError as e:
            status.set_status(str(e))
            return

        if duration_input.lower() == "program":
            duration_minutes = self._program_duration(start_time, status)
            if duration_minutes is None:
                return
        else:
            try:
                duration_minutes = int(duration_input)
                if duration_minutes <= 0:
                    raise ValueError
            except ValueError:
                status.set_status("Duration must be minutes or the word 'program'")
                return

        result = actions.schedule_live_recording(self.item, start_time, duration_minutes)
        status.set_status(result["message"])
        self.app.notify(result["message"])
        if result["success"]:
            self.app.pop_screen()

    def _program_duration(self, start_time: datetime, status: StatusBar) -> int | None:
        """Minutes from start_time to the end of the show airing then, or None."""
        program = iptv_provider.get_program_at(
            self.item.get("stream_id", 0),
            start_time.timestamp(),
            channel_name=self.item.get("name"),
            stream_url=self.item.get("stream_url"),
        )
        end_time = program.get("end") if program else None
        if not end_time or end_time <= start_time.timestamp():
            status.set_status(
                "No guide data for that time; enter a number of minutes instead"
            )
            return None

        minutes = max(1, math.ceil((end_time - start_time.timestamp()) / 60))
        title = (program.get("title") or "program").strip()
        status.set_status(f"Recording '{title}' for {minutes} minutes")
        return minutes

    def action_pop(self) -> None:
        self.app.pop_screen()
