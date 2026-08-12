"""Minimal status bar widget."""

from textual.widgets import Static


class StatusBar(Static):
    """A simple status bar line."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        content-align: left middle;
        background: $surface;
        color: $foreground;
    }
    """

    def set_status(self, text: str) -> None:
        self.update(text)
