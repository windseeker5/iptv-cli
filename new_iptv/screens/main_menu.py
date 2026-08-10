"""Main menu screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class MainMenuScreen(Screen):
    """Main menu with navigation."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "push_search", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader("IPTV")
        yield StatusBar("Ready")
        yield ListView(
            ListItem(Label("Search")),
            ListItem(Label("Favorites")),
            ListItem(Label("Browse by Category")),
            ListItem(Label("Scheduled Recordings")),
            ListItem(Label("Background Downloads")),
            ListItem(Label("Container Status")),
            ListItem(Label("Settings / Quit")),
            id="main-menu",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        labels = ["search", "favorites", "browse", "recordings", "downloads", "containers", "settings"]
        action = labels[index] if index < len(labels) else "settings"

        if action == "search":
            self.app.push_screen("search")
        elif action == "settings":
            self.app.action_quit()
        else:
            status = self.query_one(StatusBar)
            status.set_status(f"{action.title()} not yet implemented")

    def action_push_search(self) -> None:
        self.app.push_screen("search")
