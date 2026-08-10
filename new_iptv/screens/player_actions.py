"""Player actions screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import actions
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class PlayerActionsScreen(Screen):
    """Context menu for a selected item."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    ACTIONS = {
        "live": ["Play", "Restream", "Record", "Schedule Recording", "Info", "Back"],
        "vod": ["Play", "Restream", "Download", "Info", "Back"],
        "series": ["Browse Episodes", "Download Series", "Info", "Back"],
    }

    def __init__(self, result_type: str, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.result_type = result_type
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(self.item.get("name", "Actions"))
        yield StatusBar("Select an action")
        actions_list = self.ACTIONS.get(self.result_type, self.ACTIONS["live"])
        yield ListView(*[ListItem(Label(action)) for action in actions_list])
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action_label = event.item.children[0].renderable
        status = self.query_one(StatusBar)

        if action_label == "Play":
            result = actions.play_item(self.item, self.result_type)
            status.set_status(result["message"])
        elif action_label == "Restream":
            result = actions.restream_item(self.item)
            status.set_status(result["message"])
        elif action_label == "Record":
            result = actions.record_live_item(self.item)
            status.set_status(result["message"])
        elif action_label == "Schedule Recording":
            # TODO: open a scheduling input screen
            status.set_status("Scheduling input screen not yet implemented")
        elif action_label == "Download":
            result = actions.download_vod_item(self.item)
            status.set_status(result["message"])
        elif action_label == "Download Series":
            result = actions.download_series(self.item)
            status.set_status(result["message"])
        elif action_label == "Browse Episodes":
            # TODO: open series episodes screen
            status.set_status("Series episodes screen not yet implemented")
        elif action_label == "Info":
            # TODO: open info screen
            status.set_status(f"Info: {self.item.get('name')}")
        elif action_label == "Back":
            self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
