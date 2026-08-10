"""Favorites screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import favorites as favorites_domain
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class FavoritesScreen(Screen):
    """Browse favorites."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("p", "play_selected", "Play"),
        ("r", "restream_selected", "Restream"),
        ("d", "remove_selected", "Remove"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.favorites = []

    def compose(self) -> ComposeResult:
        yield AppHeader("Favorites")
        yield StatusBar("Loading favorites...")
        yield ListView(id="favorites-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load_favorites)

    async def _load_favorites(self) -> None:
        raw = favorites_domain.load_favorites()
        self.favorites = favorites_domain.hydrate_favorites_with_database(raw)

        list_view = self.query_one("#favorites-list", ListView)
        list_view.clear()

        if not self.favorites:
            self.query_one(StatusBar).set_status("No favorites. Press 's' while searching to add some.")
            return

        for idx, item in enumerate(self.favorites):
            item_type = item.get("type", "live")
            name = item.get("name", "Unknown")
            category = item.get("category", "Uncategorized")
            prefix = "[LIVE]" if item_type == "live" else "[VOD]"
            list_view.append(ListItem(Label(f"{prefix} {name}  ({category})"), name=f"{idx}"))

        self.query_one(StatusBar).set_status(
            "Enter=actions  p=play  r=restream  d=remove  esc=back"
        )
        if self.favorites:
            list_view.index = 0
            list_view.focus()

    def _selected_item(self) -> dict | None:
        list_view = self.query_one("#favorites-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.favorites):
            return self.favorites[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = self._selected_item()
        if item:
            from new_iptv.screens.player_actions import PlayerActionsScreen
            self.app.push_screen(PlayerActionsScreen(item.get("type", "live"), item))

    def action_play_selected(self) -> None:
        item = self._selected_item()
        if item:
            self.query_one(StatusBar).set_status(f"Play: {item.get('name')}")

    def action_restream_selected(self) -> None:
        item = self._selected_item()
        if item and item.get("type") != "series":
            self.query_one(StatusBar).set_status(f"Restream: {item.get('name')}")

    def action_remove_selected(self) -> None:
        item = self._selected_item()
        if item:
            favorites_domain.remove_favorite(item, item.get("type", "live"))
            self._load_favorites()
            self.query_one(StatusBar).set_status("Removed from favorites")

    def action_pop(self) -> None:
        self.app.pop_screen()
