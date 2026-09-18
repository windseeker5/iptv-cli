"""Unified downloads and recordings screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import ListView, ListItem, Label

from iptv_tui.domain import db, jobs
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class DownloadsScreen(Screen):
    """Live view of every download and recording, active or scheduled."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("r", "refresh", "Refresh"),
        ("x", "cancel_selected", "Cancel"),
        ("l", "logs_selected", "Logs"),
        ("X", "clear_all", "Clear All"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = []

    def compose(self) -> ComposeResult:
        yield AppHeader("Recording & Download Queue")
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

        self.query_one(StatusBar).set_status(
            "L view log  •  X cancel selected  •  R refresh  •  Shift+X clear all"
        )
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

    def action_logs_selected(self) -> None:
        row = self._selected_row()
        if not row or row.get("kind") != "scheduled":
            self.query_one(StatusBar).set_status("Logs are available for scheduled recordings")
            return

        output_path = row.get("output_path") or ""
        logs = sorted((db.data_dir() / "logs").glob("recording_*.log"), reverse=True)
        matching = []
        for path in logs:
            try:
                if output_path and output_path in path.read_text(errors="replace"):
                    matching.append(path)
            except OSError:
                continue
        log_path = matching[0] if matching else None
        if not log_path:
            self.query_one(StatusBar).set_status("No recording log is available")
            return

        from iptv_tui.screens.log_viewer import LogViewerScreen
        self.app.push_screen(
            LogViewerScreen(log_path.name, log_path.read_text(errors="replace"))
        )

    def action_clear_all(self) -> None:
        from iptv_tui.screens.clear_all import ClearAllConfirmScreen
        self.app.push_screen(ClearAllConfirmScreen())

    def action_pop(self) -> None:
        self.app.pop_screen()
