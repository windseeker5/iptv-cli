"""Main menu screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

import asyncio

from new_iptv.domain import config, favorites, iptv_provider
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class MainMenuScreen(Screen):
    """Main menu with navigation."""

    BINDINGS = [
        Binding("s", "push_search", "Search", show=False),
    ]

    MENU_ITEMS = [
        ("Update Database", "update_db"),
        ("Search", "search"),
        ("Favorites", "favorites"),
        ("Browse by Category", "browse"),
        ("Scheduled Recordings", "recordings"),
        ("Background Downloads", "downloads"),
        ("YouTube Tool", "youtube"),
        ("Settings", "settings"),
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

    def on_mount(self) -> None:
        self.run_worker(self._background_refresh)

    async def _background_refresh(self) -> None:
        status = self.query_one(StatusBar)
        try:
            counts = iptv_provider.db.table_counts()
            live = counts.get("live_streams", 0)
            if live == 0:
                status.set_status("Database empty — downloading from provider...")
                result = await asyncio.to_thread(iptv_provider.download_database)
                if result["success"]:
                    status.set_status(result["message"])
                else:
                    status.set_status(f"DB download: {result['message']}")
                    return
            # Refresh EPG after we know the channel list.
            epg_result = await asyncio.to_thread(iptv_provider.download_full_epg)
            if not epg_result["success"]:
                status.set_status(f"EPG: {epg_result['message']}")
        except Exception as exc:
            status.set_status(f"Refresh error: {exc}")

    async def _run_db_update(self) -> None:
        status = self.query_one(StatusBar)
        status.set_status("Downloading database from provider...")
        try:
            result = await asyncio.to_thread(iptv_provider.download_database)
            status.set_status(result["message"])
        except Exception as exc:
            status.set_status(f"DB download error: {exc}")

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
        if action == "update_db":
            self.run_worker(self._run_db_update)
        elif action == "search":
            self.app.push_screen("search")
        elif action == "favorites":
            from new_iptv.screens.favorites import FavoritesScreen
            self.app.push_screen(FavoritesScreen())
        elif action == "browse":
            from new_iptv.screens.category_browser import CategoryBrowserScreen
            self.app.push_screen(CategoryBrowserScreen())
        elif action == "recordings":
            from new_iptv.screens.scheduled_recordings import ScheduledRecordingsScreen
            self.app.push_screen(ScheduledRecordingsScreen())
        elif action == "downloads":
            from new_iptv.screens.background_downloads import BackgroundDownloadsScreen
            self.app.push_screen(BackgroundDownloadsScreen())
        elif action == "settings":
            self.app.push_screen("settings")
        elif action == "youtube":
            from new_iptv.screens.youtube import YouTubeScreen
            self.app.push_screen(YouTubeScreen())

    def action_push_search(self) -> None:
        self.app.push_screen("search")
