"""Simple message screen for alerts and errors."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button

from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class MessageScreen(Screen):
    """Display a message with an OK button."""

    BINDINGS = [
        ("escape", "close", "Close"),
        ("q", "close", "Close"),
    ]

    def __init__(
        self, message: str, title: str = "Message", close_app: bool = False, **kwargs
    ):
        super().__init__(**kwargs)
        self.message = message
        self.title = title
        self.close_app = close_app

    def compose(self) -> ComposeResult:
        yield AppHeader(self.title)
        yield StatusBar("Press OK or Escape")
        yield Static(self.message)
        yield Button("OK", id="ok-button", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-button":
            self.action_close()

    def action_close(self) -> None:
        if self.close_app:
            self.app.action_quit()
        else:
            self.app.pop_screen()
