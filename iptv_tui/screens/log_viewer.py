"""Scrollable in-app log viewer."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class LogViewerScreen(Screen):
    """Display diagnostic text without writing behind the TUI."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("q", "pop", "Back"),
    ]

    def __init__(self, title: str, content: str, **kwargs):
        super().__init__(**kwargs)
        self.log_title = title
        self.content = content or "No log output available."

    def compose(self) -> ComposeResult:
        yield AppHeader(self.log_title)
        yield StatusBar("Use arrow keys or Page Up/Down to scroll; Escape returns")
        with VerticalScroll(id="log-scroll"):
            yield Static(self.content, id="log-content")

    def action_pop(self) -> None:
        self.app.pop_screen()
