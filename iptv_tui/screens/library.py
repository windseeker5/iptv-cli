"""Completed recordings and downloads library."""

import asyncio
from datetime import datetime

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Button, Label, ListItem, ListView, Static

from iptv_tui.domain import actions, library, remux
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


class LibraryScreen(Screen):
    """Browse and act on completed media files."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("p", "play_selected", "Play"),
        ("m", "remux_selected", "Make TV compatible"),
        ("x", "delete_selected", "Delete"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.rows = []

    def compose(self) -> ComposeResult:
        yield AppHeader("My Videos")
        yield StatusBar("P play  •  M make TV compatible  •  X delete  •  R refresh")
        yield ListView(id="library-list")

    def on_mount(self) -> None:
        self.action_refresh()

    async def _load(self) -> None:
        self.rows = await asyncio.to_thread(library.list_media)
        view = self.query_one("#library-list", ListView)
        view.clear()
        if not self.rows:
            self.query_one(StatusBar).set_status(
                "No completed media yet. Record a channel or download something."
            )
            return
        for idx, row in enumerate(self.rows):
            when = datetime.fromtimestamp(row["modified"]).strftime("%Y-%m-%d %H:%M")
            label = f"[{row['kind']}] {row['name']}  {_human_size(row['size'])}  {when}"
            view.append(ListItem(Label(label), name=str(idx)))
        view.index = 0
        view.focus()

    def _selected(self) -> dict | None:
        view = self.query_one("#library-list", ListView)
        idx = view.index if view.index is not None else -1
        return self.rows[idx] if 0 <= idx < len(self.rows) else None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_play_selected()

    def action_play_selected(self) -> None:
        row = self._selected()
        if row:
            result = actions.play_url(str(row["path"]))
            self.query_one(StatusBar).set_status(result["message"])

    def action_remux_selected(self) -> None:
        row = self._selected()
        if not row:
            return
        if row["path"].suffix.lower() == ".mp4":
            self.query_one(StatusBar).set_status("This file is already MP4")
            return
        self.query_one(StatusBar).set_status(f"Making {row['name']} TV compatible...")
        self.run_worker(self._remux(row), exclusive=True)

    async def _remux(self, row: dict) -> None:
        result = await asyncio.to_thread(remux.remux_for_tv, str(row["path"]))
        self.query_one(StatusBar).set_status(result["message"])
        self.app.notify(result["message"])
        await self._load()

    def action_delete_selected(self) -> None:
        row = self._selected()
        if row:
            self.app.push_screen(DeleteMediaConfirmScreen(row, self))

    def action_refresh(self) -> None:
        self.run_worker(self._load, exclusive=True)

    def action_pop(self) -> None:
        self.app.pop_screen()


class DeleteMediaConfirmScreen(Screen):
    """Confirm deletion of one media file."""

    BINDINGS = [("escape", "pop", "Cancel")]

    def __init__(self, row: dict, library_screen: LibraryScreen, **kwargs):
        super().__init__(**kwargs)
        self.row = row
        self.library_screen = library_screen

    def compose(self) -> ComposeResult:
        yield AppHeader("Delete media?")
        yield StatusBar("Choose Delete permanently or Cancel")
        yield Static(f"Delete this file permanently?\n\n{self.row['name']}")
        yield Button("Delete permanently", id="delete", variant="error")
        yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete":
            deleted = library.delete_media(self.row["path"])
            self.app.pop_screen()
            self.library_screen.action_refresh()
            self.app.notify("File deleted" if deleted else "File could not be deleted")
        elif event.button.id == "cancel":
            self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
