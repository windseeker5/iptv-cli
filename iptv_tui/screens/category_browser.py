"""Browse categories screen."""

import asyncio

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import ListView, ListItem, Label

from iptv_tui.domain import iptv_provider
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class CategoryBrowserScreen(Screen):
    """Browse live, movie, or series categories."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("l", "mode_live", "Live"),
        ("v", "mode_vod", "Movies"),
        ("s", "mode_series", "Series"),
    ]

    def __init__(self, mode: str = "live", category: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        self.category = category
        self.items = []

    def compose(self) -> ComposeResult:
        mode_label = {"live": "Live TV", "vod": "Movies", "series": "Series"}.get(
            self.mode, self.mode.title()
        )
        yield AppHeader(f"Browse {mode_label}")
        yield StatusBar("Loading...")
        yield ListView(id="category-list")

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        list_view = self.query_one("#category-list", ListView)
        list_view.clear()

        if self.category is None:
            # Show categories
            if self.mode == "live":
                categories = await asyncio.to_thread(iptv_provider.get_live_categories)
            elif self.mode == "vod":
                categories = await asyncio.to_thread(iptv_provider.get_vod_categories)
            else:
                categories = await asyncio.to_thread(iptv_provider.get_series_categories)

            for cat in categories:
                name = cat.get("category_name", "Unknown")
                list_view.append(ListItem(Label(name), name=name))

            self.query_one(StatusBar).set_status("")
        else:
            # Show items in category
            if self.mode == "live":
                self.items = await asyncio.to_thread(
                    iptv_provider.get_channels_by_category, self.category
                )
                for idx, item in enumerate(self.items):
                    list_view.append(ListItem(Label(item.get("name", "Unknown")), name=f"{idx}"))
            elif self.mode == "vod":
                self.items = await asyncio.to_thread(
                    iptv_provider.get_vod_by_category, self.category
                )
                for idx, item in enumerate(self.items):
                    year = item.get("year") or "N/A"
                    name = item.get("name", "Unknown")
                    list_view.append(ListItem(Label(f"{year} {name}"), name=f"{idx}"))
            else:
                self.items = await asyncio.to_thread(
                    iptv_provider.get_series_by_category, self.category
                )
                for idx, item in enumerate(self.items):
                    rating = item.get("rating") or "N/A"
                    name = item.get("name", "Unknown")
                    list_view.append(ListItem(Label(f"{rating}  {name}"), name=f"{idx}"))

            self.query_one(StatusBar).set_status(
                "Enter to open  •  L live  •  V movies  •  S series  •  Escape back"
            )

        if list_view.children:
            list_view.index = 0
            list_view.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.category is None:
            selected_name = event.item.name
            self.app.push_screen(CategoryBrowserScreen(self.mode, selected_name))
        else:
            idx = event.list_view.index
            if 0 <= idx < len(self.items):
                if self.mode == "series":
                    from iptv_tui.screens.series_episodes import SeriesEpisodesScreen
                    self.app.push_screen(SeriesEpisodesScreen(self.items[idx]))
                else:
                    from iptv_tui.screens.player_actions import PlayerActionsScreen
                    self.app.push_screen(PlayerActionsScreen(self.mode, self.items[idx]))

    def action_mode_live(self) -> None:
        self.app.push_screen(CategoryBrowserScreen("live"))

    def action_mode_vod(self) -> None:
        self.app.push_screen(CategoryBrowserScreen("vod"))

    def action_mode_series(self) -> None:
        self.app.push_screen(CategoryBrowserScreen("series"))

    def action_pop(self) -> None:
        self.app.pop_screen()
