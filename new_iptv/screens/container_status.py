"""Container status screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, ListView, ListItem, Label, Static

from new_iptv.domain import docker_ctl
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


class ContainerStatusScreen(Screen):
    """Show service health and basic controls."""

    BINDINGS = [
        ("escape", "pop", "Back"),
        ("r", "refresh", "Refresh"),
        ("s", "start_selected", "Start"),
        ("x", "stop_selected", "Stop"),
        ("l", "logs_selected", "Logs"),
    ]

    SERVICES = ["nginx-rtmp", "jellyfin", "samba", "caddy", "viewer-counter"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.status = {}

    def compose(self) -> ComposeResult:
        yield AppHeader("Container Status")
        yield StatusBar("Loading...")
        yield Static("", id="docker-info")
        yield ListView(id="service-list")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._load)

    async def _load(self) -> None:
        info = self.query_one("#docker-info", Static)
        if not docker_ctl.check_docker_available():
            info.update("Docker is not available. Install Docker and docker-compose.")
            self.query_one(StatusBar).set_status("Docker unavailable")
            return

        self.status = docker_ctl.service_status()
        list_view = self.query_one("#service-list", ListView)
        list_view.clear()

        for service in self.SERVICES:
            svc = self.status.get(service, {})
            icon = "●" if svc.get("running") else "○"
            status = "running" if svc.get("running") else ("stopped" if svc.get("exists") else "not created")
            list_view.append(ListItem(Label(f"{icon} {service}: {status}"), name=service))

        info.update(f"Compose valid: {docker_ctl.validate_compose()['valid']}")
        self.query_one(StatusBar).set_status(
            "Enter=actions  r=refresh  s=start  x=stop  l=logs  esc=back"
        )
        if list_view.option_count:
            list_view.index = 0
            list_view.focus()

    def _selected_service(self) -> str | None:
        list_view = self.query_one("#service-list", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.SERVICES):
            return self.SERVICES[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        service = self._selected_service()
        if service:
            self.query_one(StatusBar).set_status(f"Selected: {service} — use s/x/l keys")

    def action_refresh(self) -> None:
        self._load()

    def action_start_selected(self) -> None:
        service = self._selected_service()
        if service:
            result = docker_ctl.start_service(service)
            self.query_one(StatusBar).set_status(
                f"Start {service}: {'OK' if result['success'] else 'failed'}"
            )
            self._load()

    def action_stop_selected(self) -> None:
        service = self._selected_service()
        if service:
            result = docker_ctl.stop_service(service)
            self.query_one(StatusBar).set_status(
                f"Stop {service}: {'OK' if result['success'] else 'failed'}"
            )
            self._load()

    def action_logs_selected(self) -> None:
        service = self._selected_service()
        if service:
            logs = docker_ctl.service_logs(service)
            self.query_one(StatusBar).set_status(f"Showing logs for {service}")
            # For now, just print to console; a log viewer screen would be next.
            print(logs[:2000])

    def action_pop(self) -> None:
        self.app.pop_screen()
