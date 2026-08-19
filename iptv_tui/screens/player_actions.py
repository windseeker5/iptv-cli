"""Player actions screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, ListView, ListItem, Label

from iptv_tui.domain import actions
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class PlayerActionsScreen(Screen):
    """Context menu for a selected item."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    ACTIONS = {
        "live": ["Play", "Restream", "Record", "Schedule Recording", "Info", "Back"],
        "vod": ["Play", "Restream", "Download", "Download (TV-compatible)", "Info", "Back"],
        "series": ["Browse Episodes", "Download Series", "Download Series (TV-compatible)", "Info", "Back"],
    }

    def __init__(self, result_type: str, item: dict, **kwargs):
        super().__init__(**kwargs)
        self.result_type = result_type
        self.item = item

    def compose(self) -> ComposeResult:
        yield AppHeader(self.item.get("name", "Actions"))
        yield StatusBar("Select an action")
        actions_list = self.ACTIONS.get(self.result_type, self.ACTIONS["live"])
        yield ListView(*[ListItem(Label(action), name=action) for action in actions_list])

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action_label = event.item.name
        status = self.query_one(StatusBar)

        if action_label == "Play":
            result = actions.play_item(self.item, self.result_type)
            status.set_status(result["message"])
        elif action_label == "Restream":
            result = actions.restream_item(self.item)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Record":
            result = actions.record_live_item(self.item)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Schedule Recording":
            from iptv_tui.screens.schedule_recording import ScheduleRecordingScreen
            self.app.push_screen(ScheduleRecordingScreen(self.item))
        elif action_label == "Download":
            result = actions.download_vod_item(self.item)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Download (TV-compatible)":
            result = actions.download_vod_item(self.item, tv_compatible=True)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Download Series":
            result = actions.download_series(self.item)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Download Series (TV-compatible)":
            result = actions.download_series(self.item, tv_compatible=True)
            status.set_status(result["message"])
            self.app.notify(result["message"])
        elif action_label == "Browse Episodes":
            from iptv_tui.screens.series_episodes import SeriesEpisodesScreen
            self.app.push_screen(SeriesEpisodesScreen(self.item))
        elif action_label == "Info":
            from iptv_tui.screens.info import InfoScreen
            self.app.push_screen(InfoScreen(self.result_type, self.item))
        elif action_label == "Back":
            self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
