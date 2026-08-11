"""Browse categories screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import iptv_provider
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class CategoryBrowserScreen(Screen):
    """Browse live categories and their channels, or VOD categories."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("l", "mode_live", "Live"),
        ("v", "mode_vod", "VOD"),
    ]

    def __init__(self, mode: str = "live", category: str | None = None, **kwargs):
        super().__init__(**kwargs)
        self.mode = mode
        self.category = category
        self.items = []

    def compose(self) -> ComposeResult:
        yield AppHeader(f"Browse {'Live' if self.mode == 'live' else 'VOD'}")
        yield StatusBar("Loading...")
        yield ListView(id="category-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        list_view = self.query_one("#category-list", ListView)
        list_view.clear()

        if self.category is None:
            # Show categories
            if self.mode == "live":
                categories = iptv_provider.get_live_categories()
            else:
                categories = iptv_provider.get_vod_categories()

            for cat in categories:
                name = cat.get("category_name", "Unknown")
                list_view.append(ListItem(Label(name), name=name))

            self.query_one(StatusBar).set_status(
                "Enter=open  l=live  v=vod  esc=back"
            )
        else:
            # Show items in category
            if self.mode == "live":
                self.items = iptv_provider.get_channels_by_category(self.category)
                for idx, item in enumerate(self.items):
                    list_view.append(ListItem(Label(item.get("name", "Unknown")), name=f"{idx}"))
            else:
                self.items = iptv_provider.get_vod_by_category(self.category)
                for idx, item in enumerate(self.items):
                    year = item.get("year") or "N/A"
                    name = item.get("name", "Unknown")
                    list_view.append(ListItem(Label(f"{year} {name}"), name=f"{idx}"))

            self.query_one(StatusBar).set_status(
                "Enter=actions  p=play  i=info  esc=back"
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
                from new_iptv.screens.player_actions import PlayerActionsScreen
                self.app.push_screen(PlayerActionsScreen(self.mode, self.items[idx]))

    def action_mode_live(self) -> None:
        self.app.push_screen(CategoryBrowserScreen("live"))

    def action_mode_vod(self) -> None:
        self.app.push_screen(CategoryBrowserScreen("vod"))

    def action_pop(self) -> None:
        self.app.pop_screen()
