"""Series episodes browser."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import actions, downloads, iptv_provider
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class SeriesEpisodesScreen(Screen):
    """Browse and act on series episodes."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("p", "play_selected", "Play"),
        ("d", "download_selected", "Download"),
    ]

    def __init__(self, series_item: dict, **kwargs):
        super().__init__(**kwargs)
        self.series_item = series_item
        self.episodes = []

    def compose(self) -> ComposeResult:
        yield AppHeader(self.series_item.get("name", "Series"))
        yield StatusBar("Loading episodes...")
        yield ListView(id="episodes-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        series_id = self.series_item.get("series_id")
        self.episodes = iptv_provider.get_series_episodes(series_id) if series_id else []
        list_view = self.query_one("#episodes-list", ListView)
        list_view.clear()

        if not self.episodes:
            self.query_one(StatusBar).set_status("No episodes found")
            return

        for idx, ep in enumerate(self.episodes):
            s = ep.get("season_number", 0)
            e = ep.get("episode_num", 0)
            title = ep.get("title") or f"Episode {e}"
            label = f"S{s:02d}E{e:02d}  {title}"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        self.query_one(StatusBar).set_status(
            "Enter=actions  p=play  d=download  esc=back"
        )
        if list_view.option_count:
            list_view.index = 0
            list_view.focus()

    def _selected_episode(self) -> dict | None:
        list_view = self.query_one("#episodes-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.episodes):
            return self.episodes[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        ep = self._selected_episode()
        if ep:
            from new_iptv.screens.player_actions import PlayerActionsScreen
            self.app.push_screen(PlayerActionsScreen("vod", ep))

    def action_play_selected(self) -> None:
        ep = self._selected_episode()
        if ep:
            result = actions.play_item(ep, "vod")
            self.query_one(StatusBar).set_status(result["message"])

    def action_download_selected(self) -> None:
        ep = self._selected_episode()
        if ep:
            result = downloads.start_vod_download(ep)
            self.query_one(StatusBar).set_status(result["message"])

    def action_pop(self) -> None:
        self.app.pop_screen()
