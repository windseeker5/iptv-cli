"""Main menu screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import config, favorites, iptv_provider
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class MainMenuScreen(Screen):
    """Main menu with navigation."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("s", "push_search", "Search"),
    ]

    MENU_ITEMS = [
        ("Search", "search"),
        ("Favorites", "favorites"),
        ("Browse by Category", "browse"),
        ("Scheduled Recordings", "recordings"),
        ("Background Downloads", "downloads"),
        ("Container Status", "containers"),
        ("Settings / Quit", "quit"),
    ]

    def compose(self) -> ComposeResult:
        fav_count = len(favorites.load_favorites())
        options = []
        for label, action in self.MENU_ITEMS:
            display = label
            if action == "favorites" and fav_count:
                display = f"{label}  ({fav_count})"
            options.append((display, action))

        yield AppHeader("IPTV")
        yield StatusBar(self._status_text())
        yield ListView(
            *[ListItem(Label(label), name=action) for label, action in options],
            id="main-menu",
        )
        yield Footer()

    def _status_text(self) -> str:
        parts = []
        try:
            counts = iptv_provider.db.table_counts()
            live = counts.get("live_streams", 0)
            vod = counts.get("vod_streams", 0)
            parts.append(f"DB: {live:,} live / {vod:,} VOD")
        except Exception:
            parts.append("DB: unavailable")

        parts.append(f"http://localhost:{config.Config.NGINX_HTTP_PORT}")
        return "  |  ".join(parts)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.name
        if action == "search":
            self.app.push_screen("search")
        elif action == "favorites":
            from new_iptv.screens.favorites import FavoritesScreen
            self.app.push_screen(FavoritesScreen())
        elif action == "browse":
            from new_iptv.screens.category_browser import CategoryBrowserScreen
            self.app.push_screen(CategoryBrowserScreen())
        elif action == "recordings":
            self.query_one(StatusBar).set_status("Scheduled recordings screen not yet implemented")
        elif action == "downloads":
            self.query_one(StatusBar).set_status("Background downloads screen not yet implemented")
        elif action == "containers":
            from new_iptv.screens.container_status import ContainerStatusScreen
            self.app.push_screen(ContainerStatusScreen())
        elif action == "quit":
            self.app.action_quit()

    def action_push_search(self) -> None:
        self.app.push_screen("search")
