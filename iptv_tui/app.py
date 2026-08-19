"""Textual app entrypoint."""

from textual.app import App

from iptv_tui.domain import config
from iptv_tui.screens.main_menu import MainMenuScreen
from iptv_tui.screens.search import SearchScreen
from iptv_tui.screens.results import ResultsScreen
from iptv_tui.screens.player_actions import PlayerActionsScreen
from iptv_tui.screens.container_status import ContainerStatusScreen
from iptv_tui.screens.message import MessageScreen


class IPTVApp(App):
    """Minimal IPTV TUI application."""

    CSS_PATH = "styles.tcss"

    SCREENS = {
        "main": MainMenuScreen,
        "search": SearchScreen,
        "results": ResultsScreen,
        "actions": PlayerActionsScreen,
        "settings": ContainerStatusScreen,
        "message": MessageScreen,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._credentials_ok = config.Config.credentials_ok()

    def on_mount(self) -> None:
        self.theme = "rose-pine"
        if not self._credentials_ok:
            missing = ", ".join(config.Config.missing_credentials())
            self.push_screen(
                MessageScreen(
                    f"Missing environment variables: {missing}\n\n"
                    "Copy .env.example to .env and add your IPTV credentials.",
                    title="Configuration Required",
                )
            )
        else:
            self.push_screen("main")
