"""YouTube tool screen."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Header, Footer, Input, ListView, ListItem, Label

from new_iptv.domain import actions, youtube
from new_iptv.widgets.header import AppHeader
from new_iptv.widgets.status_bar import StatusBar


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
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#youtube-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return

        self.query_one(StatusBar).set_status("Searching...")
        self.run_worker(self._search, query)

    async def _search(self, query: str) -> None:
        if "youtube.com" in query or "youtu.be" in query:
            url = youtube.normalize_url(query)
            info = youtube.get_video_info(url)
            self.videos = [info] if info else []
        else:
            self.videos = youtube.search_videos(query)

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

        self.query_one(StatusBar).set_status(
            "Enter=actions  esc=back"
        )
        if list_view.option_count:
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
            ListItem(Label("Play")),
            ListItem(Label("Download Best (1080p MOV)")),
            ListItem(Label("Download Audio (MP3)")),
            ListItem(Label("Download 720p (MOV)")),
            ListItem(Label("Info")),
            ListItem(Label("Back")),
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = event.item.children[0].renderable
        status = self.query_one(StatusBar)
        url = self.video.get("url")

        if action == "Play":
            result = actions.play_youtube_video(url)
            status.set_status(result["message"])
        elif action == "Download Best (1080p MOV)":
            result = youtube.download_video(url, "best")
            status.set_status("Download started" if result else "Download failed")
        elif action == "Download Audio (MP3)":
            result = youtube.download_video(url, "audio")
            status.set_status("Download started" if result else "Download failed")
        elif action == "Download 720p (MOV)":
            result = youtube.download_video(url, "720p")
            status.set_status("Download started" if result else "Download failed")
        elif action == "Info":
            self.app.push_screen(YouTubeInfoScreen(self.video))
        elif action == "Back":
            self.app.pop_screen()

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
        yield Footer()

    def action_pop(self) -> None:
        self.app.pop_screen()
