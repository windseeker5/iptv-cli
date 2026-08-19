"""Main menu screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Header, ListView, ListItem, Label

import asyncio

from rich.text import Text

from iptv_tui.domain import config, docker_ctl, favorites, iptv_provider, jobs, restream
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class MainMenuScreen(Screen):
    """Main menu with navigation."""

    BINDINGS = [
        Binding("s", "push_search", "Search", show=False),
        Binding("x", "stop_restream", "Stop Restream", show=False),
        Binding("r", "stop_recording", "Stop Recording", show=False),
    ]

    MENU_ITEMS = [
        ("Update Database", "update_db"),
        ("Search", "search"),
        ("Favorites", "favorites"),
        ("Browse by Category", "browse"),
        ("Downloads & Recordings", "downloads"),
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

    def on_mount(self) -> None:
        self.run_worker(self._background_refresh)
        self.set_interval(2.0, self._refresh_status)

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

    def _refresh_status(self) -> None:
        self.query_one(StatusBar).set_status(self._status_text())

    def _status_text(self) -> Text:
        status = Text()

        # Database indicator
        try:
            counts = iptv_provider.db.table_counts()
            live = counts.get("live_streams", 0)
            vod = counts.get("vod_streams", 0)
            db_ok = live > 0
            status.append("🟢 " if db_ok else "⚪ ")
            status.append(f"DB: {live:,} live / {vod:,} VOD")
        except Exception:
            status.append("⚪ DB: unavailable", style="dim")

        status.append("  |  ", style="dim")

        # Local HTTP endpoint indicator (NGINX-RTMP host up/down)
        nginx_running = False
        try:
            if docker_ctl.check_docker_available():
                nginx_running = docker_ctl.container_status("iptv-nginx-rtmp").get("running", False)
        except Exception:
            pass
        status.append("🟢 " if nginx_running else "⚪ ")
        status.append(f"http://localhost:{config.Config.NGINX_HTTP_PORT}")

        # Active restream indicator
        try:
            active = restream.get_active_restream()
        except Exception:
            active = None
        if active:
            channel = active.get("channel_name", "Unknown")
            status.append("  |  ", style="dim")
            status.append("🟢 ")
            status.append(f"RESTREAM: {channel}")

        # Active recording indicator
        recording_jobs = [
            row for row in jobs.list_jobs()
            if row["type"] == "live" and row["status"] == "running"
        ]
        if recording_jobs:
            status.append("  |  ", style="dim")
            status.append("🟢 ")
            if len(recording_jobs) == 1:
                status.append(f"REC: {recording_jobs[0]['title']}")
            else:
                status.append(f"REC: {len(recording_jobs)} active")

        return status

    def action_stop_restream(self) -> None:
        result = restream.stop_restream()
        self.query_one(StatusBar).set_status(result["message"])
        self._refresh_status()

    def action_stop_recording(self) -> None:
        recording_jobs = [
            row for row in jobs.list_jobs()
            if row["type"] == "live" and row["status"] == "running"
        ]
        if not recording_jobs:
            self.query_one(StatusBar).set_status("No active recording")
            return
        for row in recording_jobs:
            jobs.cancel(row)
        message = (
            "Recording stopped" if len(recording_jobs) == 1
            else f"{len(recording_jobs)} recordings stopped"
        )
        self.query_one(StatusBar).set_status(message)
        self.app.notify(message)
        self._refresh_status()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.name
        if action == "update_db":
            self.run_worker(self._run_db_update)
        elif action == "search":
            self.app.push_screen("search")
        elif action == "favorites":
            from iptv_tui.screens.favorites import FavoritesScreen
            self.app.push_screen(FavoritesScreen())
        elif action == "browse":
            from iptv_tui.screens.category_browser import CategoryBrowserScreen
            self.app.push_screen(CategoryBrowserScreen())
        elif action == "downloads":
            from iptv_tui.screens.downloads_recordings import DownloadsScreen
            self.app.push_screen(DownloadsScreen())
        elif action == "settings":
            self.app.push_screen("settings")
        elif action == "youtube":
            from iptv_tui.screens.youtube import YouTubeScreen
            self.app.push_screen(YouTubeScreen())

    def action_push_search(self) -> None:
        self.app.push_screen("search")
