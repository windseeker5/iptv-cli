"""YouTube tool screen."""

import asyncio
from functools import partial

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Input, ListView, ListItem, Label, Static

from iptv_tui.domain import actions, jobs, youtube
from iptv_tui.widgets.header import AppHeader
from iptv_tui.widgets.status_bar import StatusBar


class YouTubeScreen(Screen):
    """Search and download YouTube videos."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.videos = []

    def compose(self) -> ComposeResult:
        yield AppHeader("YouTube Tool")
        yield StatusBar("Enter a search query")
        yield Input(placeholder="search or paste URL...", id="youtube-input")
        yield ListView(id="youtube-results")

    def on_mount(self) -> None:
        self.query_one("#youtube-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return

        self.query_one(StatusBar).set_status("Searching...")
        self.run_worker(partial(self._search, query))

    async def _search(self, query: str) -> None:
        if "youtube.com" in query or "youtu.be" in query:
            url = youtube.normalize_url(query)
            info = await asyncio.to_thread(youtube.get_video_info, url)
            self.videos = [info] if info else []
        else:
            self.videos = await asyncio.to_thread(youtube.search_videos, query)

        list_view = self.query_one("#youtube-results", ListView)
        list_view.clear()

        if not self.videos:
            self.query_one(StatusBar).set_status("No videos found")
            return

        for idx, video in enumerate(self.videos):
            duration = youtube.format_duration(video.get("duration", 0))
            title = video.get("title", "Unknown")
            uploader = video.get("uploader", "Unknown")
            label = f"{title}  |  {uploader}  [{duration}]"
            list_view.append(ListItem(Label(label), name=f"{idx}"))

        self.query_one(StatusBar).set_status("")
        if list_view.children:
            list_view.index = 0
            list_view.focus()

    def _selected_video(self) -> dict | None:
        list_view = self.query_one("#youtube-results", ListView)
        idx = list_view.index if list_view.index is not None else -1
        if 0 <= idx < len(self.videos):
            return self.videos[idx]
        return None

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        video = self._selected_video()
        if video:
            self.app.push_screen(YouTubeActionsScreen(video))

    def action_pop(self) -> None:
        self.app.pop_screen()


class YouTubeActionsScreen(Screen):
    """Actions for a selected YouTube video."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, video: dict, **kwargs):
        super().__init__(**kwargs)
        self.video = video

    def compose(self) -> ComposeResult:
        yield AppHeader(self.video.get("title", "Video"))
        yield StatusBar("Select an action")
        yield ListView(
            ListItem(Label("Play"), name="Play"),
            ListItem(Label("Download Best (1080p MOV)"), name="Download Best (1080p MOV)"),
            ListItem(Label("Download Audio (MP3)"), name="Download Audio (MP3)"),
            ListItem(Label("Download 720p (MOV)"), name="Download 720p (MOV)"),
            ListItem(Label("Info"), name="Info"),
            ListItem(Label("Back"), name="Back"),
        )

    FORMAT_CHOICES = {
        "Download Best (1080p MOV)": "best",
        "Download Audio (MP3)": "audio",
        "Download 720p (MOV)": "720p",
    }

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.name
        status = self.query_one(StatusBar)
        url = self.video.get("url")
        title = self.video.get("title", "video")

        if action == "Play":
            result = actions.play_youtube_video(url)
            status.set_status(result["message"])
        elif action in self.FORMAT_CHOICES:
            self._start_download(url, title, self.FORMAT_CHOICES[action])
        elif action == "Info":
            self.app.push_screen(YouTubeInfoScreen(self.video))
        elif action == "Back":
            self.app.pop_screen()

    def _start_download(self, url: str, title: str, format_choice: str) -> None:
        job_id = jobs.register("youtube", title)
        status = self.query_one(StatusBar)
        status.set_status(f"Downloading: {title}...")
        self.app.notify(f"Download started: {title}")
        self.run_worker(
            lambda: self._do_download(url, format_choice, job_id, status), thread=True
        )

    def _do_download(
        self, url: str, format_choice: str, job_id: str, status: StatusBar
    ) -> None:
        def progress_hook(d: dict) -> None:
            if d.get("status") == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                detail = f"{downloaded / total * 100:.0f}%" if total else "downloading"
                self.app.call_from_thread(jobs.update, job_id, detail=detail)

        result = youtube.download_video(
            url,
            format_choice,
            progress_hook=progress_hook,
            is_cancelled=lambda: jobs.cancel_requested(job_id),
        )
        ok = result["success"]
        final_status = "cancelled" if result.get("cancelled") else ("done" if ok else "failed")
        self.app.call_from_thread(
            jobs.update,
            job_id,
            status=final_status,
            detail="" if ok else result["message"],
        )
        self.app.call_from_thread(status.set_status, result["message"])
        self.app.call_from_thread(self.app.notify, result["message"])

    def action_pop(self) -> None:
        self.app.pop_screen()


class YouTubeInfoScreen(Screen):
    """Show YouTube video details."""

    BINDINGS = [
        ("escape", "pop", "Back"),
    ]

    def __init__(self, video: dict, **kwargs):
        super().__init__(**kwargs)
        self.video = video

    def compose(self) -> ComposeResult:
        yield AppHeader(self.video.get("title", "Video Info"))
        yield StatusBar("Press Escape to close")
        lines = [
            f"Title: {self.video.get('title', 'Unknown')}",
            f"Uploader: {self.video.get('uploader', 'Unknown')}",
            f"Duration: {youtube.format_duration(self.video.get('duration', 0))}",
            f"Views: {self.video.get('view_count', 'N/A')}",
            f"Likes: {self.video.get('like_count', 'N/A')}",
            f"Upload Date: {youtube.format_upload_date(self.video.get('upload_date'))}",
            f"URL: {self.video.get('url', 'N/A')}",
        ]
        yield Static("\n".join(lines), id="youtube-info-text")

    def action_pop(self) -> None:
        self.app.pop_screen()
