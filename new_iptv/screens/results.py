"""Unified results screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ResultsScreen(Screen):
    """Placeholder results screen."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, query: str = "", **kwargs):
        super().__init__(**kwargs)
        self.query = query

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Results: {self.query}")
        yield StatusBar("Showing placeholder results")
        yield ListView(
            ListItem(Label("Result 1 (placeholder)")),
            ListItem(Label("Result 2 (placeholder)")),
            ListItem(Label("Result 3 (placeholder)")),
        )
        yield Footer()

    def action_pop(self) -> None:
        self.app.pop_screen()
