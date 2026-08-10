"""Textual app entrypoint."""

from textual.app import App

from new_iptv.screens.main_menu import MainMenuScreen
from new_iptv.screens.search import SearchScreen
from new_iptv.screens.results import ResultsScreen
from new_iptv.screens.player_actions import PlayerActionsScreen
from new_iptv.screens.container_status import ContainerStatusScreen


class IPTVApp(App):
    """Minimal IPTV TUI application."""

    CSS_PATH = "styles.tcss"

    SCREENS = {
        "main": MainMenuScreen,
        "search": SearchScreen,
        "results": ResultsScreen,
        "actions": PlayerActionsScreen,
        "containers": ContainerStatusScreen,
    }

    def on_mount(self) -> None:
        self.push_screen("main")


if __name__ == "__main__":
    IPTVApp().run()
