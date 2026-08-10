"""Player actions screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class PlayerActionsScreen(Screen):
    """Placeholder actions screen."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader("Actions")
        yield StatusBar("Choose an action")
        yield ListView(
            ListItem(Label("Play")),
            ListItem(Label("Restream")),
            ListItem(Label("Record")),
            ListItem(Label("Download")),
            ListItem(Label("Info")),
            ListItem(Label("Favorite")),
        )
        yield Footer()

    def action_pop(self) -> None:
        self.app.pop_screen()
