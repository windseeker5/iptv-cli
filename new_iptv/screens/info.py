"""Info/detail screens for live, VOD, and series items."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Static, Button

from new_iptv.domain import iptv_provider
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class InfoScreen(Screen):
    """Show details for a live channel, VOD item, or series."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, result_type: str, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.result_type = result_type
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(self.item.get("name", "Info"))
        yield StatusBar("Press Escape or OK to close")
        yield Static(self._build_text(), id="info-text")
        yield Button("OK", id="ok-button", variant="primary")

    def _build_text(self) -> str:
        lines = []
        lines.append(f"Name: {self.item.get('name', 'Unknown')}")
        lines.append(f"Type: {self.result_type.upper()}")

        if self.result_type == "live":
            lines.append(f"Category: {self.item.get('category_name', 'N/A')}")
            epg = iptv_provider.get_now_playing(
                self.item.get("stream_id", 0),
                self.item.get("name"),
                self.item.get("stream_url"),
            )
            if epg and epg.get("title"):
                lines.append(f"\nNow Playing: {epg['title']}")
                if epg.get("description"):
                    lines.append(f"\n{epg['description']}")
        elif self.result_type == "vod":
            lines.append(f"Year: {self.item.get('year') or 'N/A'}")
            lines.append(f"Rating: {self.item.get('rating') or 'N/A'}")
            lines.append(f"Genre: {self.item.get('genre') or 'N/A'}")
            lines.append(f"Category: {self.item.get('category_name') or 'N/A'}")
        else:
            lines.append(f"Rating: {self.item.get('rating') or 'N/A'}")
            lines.append(f"Genre: {self.item.get('genre') or 'N/A'}")
            lines.append(f"Category: {self.item.get('category_name') or 'N/A'}")
            if self.item.get("plot"):
                lines.append(f"\n{self.item['plot']}")

        return "\n".join(lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok-button":
            self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
