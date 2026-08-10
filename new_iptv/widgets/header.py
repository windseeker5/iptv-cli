"""Main application header widget."""

from textual.widgets import Static


class AppHeader(Static):
    """Simple app header."""

    DEFAULT_CSS = """
    AppHeader {
        height: 1;
        content-align: center middle;
        background: #111111;
        color: cyan;
        text-style: bold;
    }
    """

    def __init__(self, title: str = "IPTV", **kwargs):
        super().__init__(title, **kwargs)
