"""Minimal input screen for scheduling a live recording."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Label

from new_iptv.domain import actions
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ScheduleRecordingScreen(Screen):
    """Collect a start time and duration, then schedule via systemd."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Schedule Recording — {self.item.get('name', '')}")
        yield StatusBar("Fill in both fields, press Enter on Duration to confirm")
        yield Label("Start (now / HH:MM / tomorrow HH:MM):")
        yield Input(value="now", id="start-input")
        yield Label("Duration (minutes):")
        yield Input(value="60", id="duration-input")

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
            duration_minutes = int(duration_input)
        except ValueError:
            status.set_status("Duration must be a whole number of minutes")
            return

        result = actions.schedule_live_recording(self.item, start_input, duration_minutes)
        status.set_status(result["message"])
        self.app.notify(result["message"])
        if result["success"]:
            self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
