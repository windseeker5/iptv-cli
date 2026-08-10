"""Container status screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Static

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ContainerStatusScreen(Screen):
    """Placeholder container status screen."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader("Container Status")
        yield StatusBar("Service health not yet implemented")
        yield Static("nginx-rtmp: unknown\njellyfin: unknown\nsamba: unknown")
        yield Footer()

    def action_pop(self) -> None:
        self.app.pop_screen()
