"""Scheduled recordings screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import recordings
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ScheduledRecordingsScreen(Screen):
    """View and cancel scheduled recordings."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("r", "refresh", "Refresh"),
        ("x", "cancel_selected", "Cancel"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.items = []

    def compose(self) -> ComposeResult:
        yield AppHeader("Scheduled Recordings")
        yield StatusBar("Loading...")
        yield ListView(id="recordings-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        self.items = recordings.list_recordings(limit=50)
        list_view = self.query_one("#recordings-list", ListView)
        list_view.clear()

        if not self.items:
            self.query_one(StatusBar).set_status("No scheduled recordings")
            return

        for idx, item in enumerate(self.items):
            start_dt = recordings.datetime.fromtimestamp(item["start_time"])
            duration_h = item["duration"] / 3600
            status = item.get("status", "pending")
            label = f"[{status}] {item['channel_name']}  {start_dt.strftime('%Y-%m-%d %H:%M')}  {duration_h:.1f}h"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        self.query_one(StatusBar).set_status(
            "Enter=details  x=cancel  r=refresh  esc=back"
        )
        if list_view.option_count:
            list_view.index = 0
            list_view.focus()

    def _selected_item(self) -> dict | None:
        list_view = self.query_one("#recordings-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = self._selected_item()
        if item:
            self.query_one(StatusBar).set_status(
                f"Output: {item.get('output_path', 'N/A')}"
            )

    def action_refresh(self) -> None:
        self._load()

    def action_cancel_selected(self) -> None:
        item = self._selected_item()
        if item:
            recordings.cancel_recording(item["id"])
            self.query_one(StatusBar).set_status("Recording cancelled")
            self._load()

    def action_pop(self) -> None:
        self.app.pop_screen()
