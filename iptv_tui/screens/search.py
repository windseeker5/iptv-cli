"""Search input screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Input, Static

from iptv_tui.screens.results import ResultsScreen
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class SearchScreen(Screen):
    """Search across live, VOD, and series."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader("Search")
        yield StatusBar("Type a query and press Enter")
        yield Static("Search:")
        yield Input(placeholder="channel, movie, series...", id="search-input")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.app.push_screen(ResultsScreen(query))
        else:
            self.query_one(StatusBar).set_status("Empty query, try again")

    def action_pop(self) -> None:
        self.app.pop_screen()
