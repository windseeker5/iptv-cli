"""Unified downloads and recordings screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, ListView, ListItem, Label

from new_iptv.domain import jobs
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class DownloadsScreen(Screen):
    """Live view of every download and recording, active or scheduled."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("r", "refresh", "Refresh"),
        ("x", "cancel_selected", "Cancel"),
        ("X", "clear_all", "Clear All"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = []

    def compose(self) -> ComposeResult:
        yield AppHeader("Downloads & Recordings")
        yield StatusBar("Loading...")
        yield ListView(id="jobs-list")

    def on_mount(self) -> None:
        self.run_worker(self._load)
        self.set_interval(2.0, self._refresh_silently)

    async def _load(self) -> None:
        self.rows = jobs.list_jobs()
        list_view = self.query_one("#jobs-list", ListView)

        previous_index = list_view.index
        list_view.clear()

        if not self.rows:
            self.query_one(StatusBar).set_status(
                "Nothing here yet — download or record something."
            )
            return

        for idx, row in enumerate(self.rows):
            label = f"{row['icon']} [{row['type']}] {row['title']}  {row['detail']}"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        self.query_one(StatusBar).set_status("")
        if list_view.children:
            list_view.index = min(previous_index or 0, len(list_view.children) - 1)

    async def _refresh_silently(self) -> None:
        """Auto-refresh without stealing focus or resetting the status hint."""
        self.rows = jobs.list_jobs()
        list_view = self.query_one("#jobs-list", ListView)
        previous_index = list_view.index
        had_focus = list_view.has_focus
        list_view.clear()

        for idx, row in enumerate(self.rows):
            label = f"{row['icon']} [{row['type']}] {row['title']}  {row['detail']}"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        if list_view.children:
            list_view.index = min(previous_index or 0, len(list_view.children) - 1)
            if had_focus:
                list_view.focus()

    def _selected_row(self) -> dict | None:
        list_view = self.query_one("#jobs-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.rows):
            return self.rows[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        row = self._selected_row()
        if row:
            self.query_one(StatusBar).set_status(
                f"{row['title']}  —  {row['status']}  {row['detail']}"
            )

    def action_refresh(self) -> None:
        self.run_worker(self._load)

    def action_cancel_selected(self) -> None:
        row = self._selected_row()
        if row:
            result = jobs.cancel(row)
            self.query_one(StatusBar).set_status(result["message"])
            self.app.notify(result["message"])
            self.run_worker(self._load)

    def action_clear_all(self) -> None:
        from new_iptv.screens.clear_all import ClearAllConfirmScreen
        self.app.push_screen(ClearAllConfirmScreen())

    def action_pop(self) -> None:
        self.app.pop_screen()
