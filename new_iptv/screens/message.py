"""Simple message screen for alerts and errors."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Static, Button

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class MessageScreen(Screen):
    """Display a message with an OK button."""

    BINDINGS = [
        ("escape", "quit", "Quit"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, message: str, title: str = "Message", **kwargs):
        super().__init__(**kwargs)
        self.message = message
        self.title = title

    def compose(self) -> ComposeResult:
        yield AppHeader(self.title)
        yield StatusBar("Press OK or Escape")
        yield Static(self.message)
        yield Button("OK", id="ok-button", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-button":
            self.app.action_quit()
