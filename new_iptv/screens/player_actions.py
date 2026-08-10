"""Player actions screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class PlayerActionsScreen(Screen):
    """Context menu for a selected item."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    ACTIONS = {
        "live": ["Play", "Restream", "Record", "Schedule Recording", "Info", "Toggle Favorite"],
        "vod": ["Play", "Restream", "Download", "Info", "Toggle Favorite"],
        "series": ["Browse Episodes", "Download Series", "Info", "Toggle Favorite"],
    }

    def __init__(self, result_type: str, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.result_type = result_type
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(self.item.get("name", "Actions"))
        yield StatusBar("Select an action")
        actions = self.ACTIONS.get(self.result_type, self.ACTIONS["live"])
        yield ListView(*[ListItem(Label(action)) for action in actions])
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.children[0].renderable
        self.query_one(StatusBar).set_status(f"Selected: {action}")

    def action_pop(self) -> None:
        self.app.pop_screen()
