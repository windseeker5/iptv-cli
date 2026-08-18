"""Main application header widget."""

from pyfiglet import figlet_format
from textual.widgets import Static


def _branded_header(title: str) -> str:
    if title.strip().upper() == "IPTV":
        return str(figlet_format("IPTV", font="isometric1"))
    return title


class AppHeader(Static):
    """Branded app header with optional ASCII art for IPTV."""

    DEFAULT_CSS = """
    AppHeader {
        height: auto;
        max-height: 12;
        background: $surface;
        color: $primary;
        text-style: bold;
    }
    """

    def __init__(self, title: str = "IPTV", **kwargs):
        super().__init__(_branded_header(title), **kwargs)
