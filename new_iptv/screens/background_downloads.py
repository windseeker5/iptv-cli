"""Background downloads screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label

from new_iptv.domain import downloads
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class BackgroundDownloadsScreen(Screen):
    """View series batch download jobs."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.manifests = []

    def compose(self) -> ComposeResult:
        yield AppHeader("Background Downloads")
        yield StatusBar("Loading...")
        yield ListView(id="downloads-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        self.manifests = downloads.list_batch_manifests(limit=50)
        list_view = self.query_one("#downloads-list", ListView)
        list_view.clear()

        if not self.manifests:
            self.query_one(StatusBar).set_status("No background downloads")
            return

        for idx, path in enumerate(self.manifests):
            state = downloads.read_batch_state(path)
            total = state.get("total", 0)
            completed = state.get("completed", 0)
            label = f"{path.stem}  ({completed}/{total})"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        self.query_one(StatusBar).set_status(
            "Enter=details  r=refresh  esc=back"
        )
        if list_view.option_count:
            list_view.index = 0
            list_view.focus()

    def _selected_manifest(self) -> downloads.Path | None:
        list_view = self.query_one("#downloads-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.manifests):
            return self.manifests[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        path = self._selected_manifest()
        if path:
            state = downloads.read_batch_state(path)
            self.query_one(StatusBar).set_status(
                f"Total: {state.get('total', 0)}  Completed: {state.get('completed', 0)}  Failed: {state.get('failed', 0)}"
            )

    def action_refresh(self) -> None:
        self._load()

    def action_pop(self) -> None:
        self.app.pop_screen()
