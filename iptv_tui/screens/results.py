"""Unified results screen with debounced EPG preview."""

import asyncio
import time
from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Header, ListView, ListItem, Label, Static

from iptv_tui.domain import actions, iptv_provider, favorites as favorites_domain
from iptv_tui.screens.message import MessageScreen
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class ResultsScreen(Screen):
    """Display unified search results with actions."""

    # Priority bindings fire before the ListView can consume them.
    BINDINGS = [
        Binding("escape", "pop", "Back", show=False),
        Binding("p", "play_selected", "Play", show=False, priority=True),
        Binding("i", "info_selected", "Info", show=False, priority=True),
        Binding("s", "toggle_favorite", "Star", show=False, priority=True),
        Binding("r", "restream_selected", "Restream", show=False, priority=True),
        Binding("c", "record_or_download", "Record/Download", show=False, priority=True),
    ]

    def __init__(self, query: str = "", **kwargs):
        super().__init__(**kwargs)
        self.search_query = query
        self.results = []
        self._preview_timer: Timer | None = None
        self._preview_index: int = -1

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Results: {self.search_query}")
        yield StatusBar("Loading...")
        yield Static("Searching...", id="results-info")
        yield Horizontal(
            ListView(id="results-list"),
            Static("", id="preview-panel"),
            id="results-layout",
        )

    def on_mount(self) -> None:
        self.query_one(StatusBar).set_status(
            "Loading results..."
        )
        self.run_worker(self._load_results)

    async def _load_results(self) -> None:
        self.results = []
        try:
            live = iptv_provider.search_live_channels(self.search_query)
            vod = iptv_provider.search_vod_content(self.search_query)
            series = iptv_provider.search_series_content(self.search_query)
        except Exception as exc:
            self.app.push_screen(
                MessageScreen(
                    f"Search failed: {exc}", title="Error"
                )
            )
            return

        for item in live:
            self.results.append(("live", item))
        for item in vod:
            self.results.append(("vod", item))
        for item in series:
            self.results.append(("series", item))

        favs = favorites_domain.get_favorites_set()
        list_view = self.query_one("#results-list", ListView)
        info = self.query_one("#results-info", Static)

        if not self.results:
            info.update(f"No results for '{self.search_query}'")
            self.query_one(StatusBar).set_status("No results")
            return

        info.update(f"{len(self.results)} results for '{self.search_query}'")
        self.query_one(StatusBar).set_status("")

        for idx, (result_type, item) in enumerate(self.results):
            item_id = item.get("stream_id") or item.get("series_id")
            is_fav = (item_id, result_type) in favs
            star = "★ " if is_fav else "  "
            label = self._format_item(result_type, item, star)
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        list_view.index = 0
        list_view.focus()
        self._request_preview(0)

    def _format_item(self, result_type: str, item: dict, star: str) -> str:
        name = item.get("name", "Unknown")
        if result_type == "live":
            return f"{star}[LIVE] {name}"
        elif result_type == "vod":
            rating = f"{item['rating']:.1f}" if item.get("rating") else "N/A"
            year = item.get("year") or "N/A"
            return f"{star}[VOD] {rating} {year} {name}"
        else:
            rating = f"{item['rating']:.1f}" if item.get("rating") else "N/A"
            return f"{star}[SERIES] {rating} {name}"

    def _selected_index(self) -> int:
        list_view = self.query_one("#results-list", ListView)
        return list_view.index if list_view.index is not None else -1

    def _selected_item(self) -> tuple[str, dict] | None:
        idx = self._selected_index()
        if 0 <= idx < len(self.results):
            return self.results[idx]
        return None

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        try:
            idx = int(event.item.name)
        except (ValueError, TypeError, AttributeError):
            return
        self._request_preview(idx)

    def _request_preview(self, idx: int) -> None:
        """Debounce preview updates so scrolling stays smooth."""
        if self._preview_timer is not None:
            self._preview_timer.stop()
            self._preview_timer = None

        self._preview_index = idx
        # Show an instant cache-only preview, then refresh after a short pause.
        self._update_preview(idx, live_epg=None, loading=True)
        self._preview_timer = self.set_timer(0.15, lambda: self._deferred_preview_update(idx))

    def _deferred_preview_update(self, requested_idx: int) -> None:
        """Load fresh EPG for the highlighted live item in the background."""
        if requested_idx != self._preview_index:
            return
        if requested_idx < 0 or requested_idx >= len(self.results):
            return

        result_type, item = self.results[requested_idx]
        if result_type != "live":
            self._update_preview(requested_idx)
            return

        stream_id = item.get("stream_id", 0)
        cached = iptv_provider.get_now_playing_local(stream_id)
        if cached and cached.get("title"):
            self._update_preview(requested_idx, live_epg=cached, loading=False)
            return

        # Still fresh: fetch from network without blocking scrolling.
        self.run_worker(self._fetch_preview_epg)

    async def _fetch_preview_epg(self) -> None:
        idx = self._preview_index
        if idx < 0 or idx >= len(self.results):
            return

        result_type, item = self.results[idx]
        if result_type != "live":
            return

        stream_id = item.get("stream_id", 0)
        # Run the network request in a thread so scrolling stays responsive.
        epg = await asyncio.to_thread(
            iptv_provider.get_now_playing,
            stream_id,
            item.get("name"),
            item.get("stream_url"),
        )
        if idx == self._preview_index:
            try:
                self._update_preview(idx, live_epg=epg, loading=False)
            except NoMatches:
                pass

    def _update_preview(
        self, idx: int, live_epg: dict | None = None, loading: bool = False
    ) -> None:
        preview = self.query_one("#preview-panel", Static)
        if idx < 0 or idx >= len(self.results):
            preview.update("")
            return

        result_type, item = self.results[idx]
        lines = []
        name = item.get("name", "Unknown")
        lines.append(f"[b]{name}[/b]")
        lines.append("")

        if result_type == "live":
            if live_epg and live_epg.get("title"):
                if live_epg.get("current"):
                    lines.append(f"Now: {live_epg['title']}")
                else:
                    end_ts = live_epg.get("end") or 0
                    label = "Last" if end_ts and end_ts < time.time() else "Next"
                    start_str = ""
                    start_ts = live_epg.get("start")
                    if start_ts:
                        start_str = datetime.fromtimestamp(start_ts).strftime("%a %H:%M")
                    lines.append(f"{label}: {live_epg['title']}  {start_str}")
                if live_epg.get("description"):
                    lines.append("")
                    lines.append(live_epg["description"][:300])
            elif loading:
                lines.append("Loading EPG...")
            else:
                lines.append("No EPG data available.")
        elif result_type == "vod":
            lines.append(f"Year: {item.get('year') or 'N/A'}")
            lines.append(f"Rating: {item.get('rating') or 'N/A'}")
            if item.get("genre"):
                lines.append(f"Genre: {item['genre']}")
        else:
            lines.append(f"Rating: {item.get('rating') or 'N/A'}")
            if item.get("genre"):
                lines.append(f"Genre: {item['genre']}")
            if item.get("category_name"):
                lines.append(f"Category: {item['category_name']}")
            if item.get("plot"):
                lines.append("")
                lines.append(item["plot"][:300])

        preview.update("\n".join(lines))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        selected = self._selected_item()
        if selected:
            from iptv_tui.screens.player_actions import PlayerActionsScreen
            self.app.push_screen(PlayerActionsScreen(selected[0], selected[1]))

    def action_play_selected(self) -> None:
        selected = self._selected_item()
        if selected:
            if selected[0] == "series":
                from iptv_tui.screens.series_episodes import SeriesEpisodesScreen
                self.app.push_screen(SeriesEpisodesScreen(selected[1]))
            else:
                result = actions.play_item(selected[1], selected[0])
                self.query_one(StatusBar).set_status(result["message"])

    def action_info_selected(self) -> None:
        selected = self._selected_item()
        if selected:
            from iptv_tui.screens.info import InfoScreen
            self.app.push_screen(InfoScreen(selected[0], selected[1]))

    def action_restream_selected(self) -> None:
        selected = self._selected_item()
        if selected and selected[0] != "series":
            result = actions.restream_item(selected[1])
            self.query_one(StatusBar).set_status(result["message"])
            self.app.notify(result["message"])

    def action_record_or_download(self) -> None:
        selected = self._selected_item()
        if selected and selected[0] != "series":
            if selected[0] == "live":
                result = actions.record_live_item(selected[1])
            else:
                result = actions.download_vod_item(selected[1])
            self.query_one(StatusBar).set_status(result["message"])
            self.app.notify(result["message"])

    def action_toggle_favorite(self) -> None:
        selected = self._selected_item()
        if not selected:
            return
        result_type, item = selected
        if favorites_domain.is_favorite(item, result_type):
            favorites_domain.remove_favorite(item, result_type)
            self.query_one(StatusBar).set_status("Removed from favorites")
        else:
            favorites_domain.add_favorite(item, result_type)
            self.query_one(StatusBar).set_status("Added to favorites")
        self._refresh_favorite_star(self._selected_index())

    def _refresh_favorite_star(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.results):
            return
        list_view = self.query_one("#results-list", ListView)
        result_type, item = self.results[idx]
        is_fav = favorites_domain.is_favorite(item, result_type)
        star = "★ " if is_fav else "  "
        list_view.clear()
        for i, (rt, it) in enumerate(self.results):
            list_view.append(
                ListItem(Label(self._format_item(rt, it, star)), name=f"{i}")
            )
        list_view.index = idx
        list_view.focus()

    def action_pop(self) -> None:
        self.app.pop_screen()
