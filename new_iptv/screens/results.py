"""Unified results screen."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label, Static

from new_iptv.domain import actions, iptv_provider, favorites as favorites_domain
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ResultsScreen(Screen):
    """Display unified search results with actions."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("p", "play_selected", "Play"),
        ("i", "info_selected", "Info"),
        ("s", "toggle_favorite", "Star"),
        ("r", "restream_selected", "Restream"),
        ("c", "record_or_download", "Record/Download"),
    ]

    def __init__(self, query: str = "", **kwargs):
        super().__init__(**kwargs)
        self.query = query
        self.results = []

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Results: {self.query}")
        yield StatusBar("Loading...")
        yield Static("Searching...", id="results-info")
        yield Horizontal(
            ListView(id="results-list"),
            Static("", id="preview-panel"),
            id="results-layout",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_results)

    async def _load_results(self) -> None:
        live = iptv_provider.search_live_channels(self.query)
        vod = iptv_provider.search_vod_content(self.query)
        series = iptv_provider.search_series_content(self.query)

        self.results = []
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
            info.update(f"No results for '{self.query}'")
            self.query_one(StatusBar).set_status("No results")
            return

        info.update(f"{len(self.results)} results for '{self.query}'")
        self.query_one(StatusBar).set_status(
            "Enter=actions  p=play  i=info  s=star  r=restream  c=record/download  esc=back"
        )

        for idx, (result_type, item) in enumerate(self.results):
            item_id = item.get("stream_id") or item.get("series_id")
            is_fav = (item_id, result_type) in favs
            star = "★ " if is_fav else "  "
            label = self._format_item(result_type, item, star)
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        if self.results:
            list_view.index = 0
            list_view.focus()
            self._update_preview(0)

    def _update_preview(self, idx: int) -> None:
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
            epg = iptv_provider.get_now_playing(item.get("stream_id", 0), name)
            if epg and epg.get("title"):
                lines.append(f"Now: {epg['title']}")
                if epg.get("description"):
                    lines.append("")
                    lines.append(epg["description"][:300])
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
            if item.get("plot"):
                lines.append("")
                lines.append(item["plot"][:300])

        preview.update("\n".join(lines))

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
        self._update_preview(idx)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        selected = self._selected_item()
        if selected:
            self.app.push_screen(PlayerActionsScreen(selected[0], selected[1]))

    def action_play_selected(self) -> None:
        selected = self._selected_item()
        if selected:
            result = actions.play_item(selected[1], selected[0])
            self.query_one(StatusBar).set_status(result["message"])

    def action_info_selected(self) -> None:
        selected = self._selected_item()
        if selected:
            from new_iptv.screens.info import InfoScreen
            self.app.push_screen(InfoScreen(selected[0], selected[1]))

    def action_restream_selected(self) -> None:
        selected = self._selected_item()
        if selected and selected[0] != "series":
            result = actions.restream_item(selected[1])
            self.query_one(StatusBar).set_status(result["message"])

    def action_record_or_download(self) -> None:
        selected = self._selected_item()
        if selected and selected[0] != "series":
            if selected[0] == "live":
                result = actions.record_live_item(selected[1])
            else:
                result = actions.download_vod_item(selected[1])
            self.query_one(StatusBar).set_status(result["message"])

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
        result_type, item = self.results[idx]
        is_fav = favorites_domain.is_favorite(item, result_type)
        star = "★ " if is_fav else "  "
        list_view = self.query_one("#results-list", ListView)
        # Rebuild the highlighted item label by replacing the list item.
        # Textual ListView does not expose in-place child mutation easily,
        # so we rebuild the whole list. For small result sets this is fine.
        list_view.clear()
        for i, (rt, it) in enumerate(self.results):
            list_view.append(ListItem(Label(self._format_item(rt, it, star)), name=f"{i}"))
        list_view.index = idx

    def action_pop(self) -> None:
        self.app.pop_screen()
