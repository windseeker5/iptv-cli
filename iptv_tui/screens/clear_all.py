"""Confirm-and-wipe screen for downloads/recordings history."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Static

from iptv_tui.domain import reset
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.0f} TB"


class ClearAllConfirmScreen(Screen):
    """Show exactly what will be deleted, require typing 'yes' to proceed."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.summary = reset.preview()

    def compose(self) -> ComposeResult:
        yield AppHeader("Clear All — this deletes real files")
        yield StatusBar("Type 'yes' and press Enter to confirm, or Escape to cancel")
        s = self.summary
        lines = [
            f"Downloads:   {s['downloads']['count']} files  ({_human_size(s['downloads']['bytes'])})",
            f"YouTube:     {s['youtube']['count']} files  ({_human_size(s['youtube']['bytes'])})",
            f"Recordings:  {s['recordings']['count']} files  ({_human_size(s['recordings']['bytes'])})",
            f"Series manifests: {s['manifests']}",
            f"Log files:        {s['logs']}",
            f"Scheduled recordings: {s['scheduled']} (timers will be stopped)",
            "",
            f"Total: {_human_size(s['total_bytes'])} of media, plus all tracking history.",
            "This cannot be undone.",
        ]
        yield Static("\n".join(lines), id="clear-all-summary")
        yield Input(placeholder="type 'yes' to confirm", id="confirm-input")

    def on_mount(self) -> None:
        self.query_one("#confirm-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        status = self.query_one(StatusBar)
        if event.value.strip().lower() != "yes":
            status.set_status("Cancelled — nothing was deleted")
            self.app.pop_screen()
            return

        result = reset.clear_all()
        message = (
            f"Cleared {result['downloads']['count'] + result['youtube']['count'] + result['recordings']['count']} "
            f"files ({_human_size(result['total_bytes'])}), "
            f"{result['manifests']} manifests, {result['logs']} logs, "
            f"{result['scheduled']} scheduled recordings"
        )
        status.set_status(message)
        self.app.notify(message)
        self.app.pop_screen()

    def action_pop(self) -> None:
        self.app.pop_screen()
