"""YouTube search, info, and download helpers using yt-dlp."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from new_iptv.domain import db


YOUTUBE_DIR = db.data_dir() / "youtube"


def _ensure_dir() -> None:
    YOUTUBE_DIR.mkdir(parents=True, exist_ok=True)


def normalize_url(raw_url: str) -> str:
    """Normalize a YouTube URL to a single video URL."""
    if not raw_url:
        return raw_url

    url = raw_url.strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if "youtu.be" in host:
        video_id = parsed.path.lstrip("/").split("/")[0]
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"
        return url

    if "youtube.com" in host:
        query = parse_qs(parsed.query)
        if "v" in query and query["v"]:
            clean_query = {"v": query["v"][0]}
            return urlunparse(
                (
                    "https",
                    "www.youtube.com",
                    "/watch",
                    "",
                    urlencode(clean_query),
                    "",
                )
            )

    return url


def search_videos(query: str, max_results: int = 20) -> list[dict]:
    """Search YouTube and return a list of video dicts."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "force_generic_extractor": False,
    }

    search_query = f"ytsearch{max_results}:{query}"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(search_query, download=False)
            if result and "entries" in result:
                videos = []
                for entry in result["entries"]:
                    if entry:
                        videos.append(
                            {
                                "id": entry.get("id"),
                                "title": entry.get("title", "Unknown Title"),
                                "uploader": entry.get("uploader", "Unknown Uploader"),
                                "duration": entry.get("duration", 0),
                                "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                            }
                        )
                return videos
    except Exception as e:
        print(f"Error searching YouTube: {e}")

    return []


def get_video_info(url: str) -> dict | None:
    """Fetch detailed metadata for a YouTube video URL."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "id": info.get("id"),
                "title": info.get("title"),
                "description": info.get("description"),
                "uploader": info.get("uploader"),
                "duration": info.get("duration"),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "upload_date": info.get("upload_date"),
                "url": url,
            }
    except Exception as e:
        print(f"Error fetching video info: {e}")
        return None


def format_duration(seconds: int) -> str:
    """Format seconds as MM:SS."""
    seconds = int(seconds) if seconds else 0
    return f"{seconds // 60}:{seconds % 60:02d}"


def format_upload_date(upload_date: str | None) -> str:
    """Format YYYYMMDD as YYYY-MM-DD."""
    if upload_date and len(str(upload_date)) == 8:
        d = str(upload_date)
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return upload_date or "Unknown"


def build_download_options(format_choice: str) -> dict:
    """Return yt-dlp options for a chosen format preset."""
    _ensure_dir()
    outtmpl = str(YOUTUBE_DIR / "%(title)s.%(ext)s")

    # Prefer the "android" client: when yt-dlp can't solve YouTube's JS
    # signature challenge (e.g. no JS runtime available), it silently falls
    # back to "android_vr", whose stream URLs frequently 403 mid-download.
    # "web"/"tv" are listed as further fallbacks in case "android" ever
    # stops working on its own.
    extractor_args = {"youtube": {"player_client": ["android", "web", "tv"]}}

    # yt-dlp has no default network timeout, so a stalled connection (a
    # common YouTube CDN hiccup) hangs forever with no error. 30s makes a
    # stall fail loudly instead of leaving a download stuck indefinitely.
    common = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "extractor_args": extractor_args,
        "socket_timeout": 30,
    }

    if format_choice == "best":
        return {
            **common,
            "format": "bestvideo[height<=1080]+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mov",
                }
            ],
        }
    if format_choice == "audio":
        return {
            **common,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    if format_choice == "720p":
        return {
            **common,
            "format": "bestvideo[height<=720]+bestaudio/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mov",
                }
            ],
        }
    raise ValueError(f"Unknown format choice: {format_choice}")


def download_video(
    url: str, format_choice: str, progress_hook=None, is_cancelled=None
) -> dict:
    """Download a YouTube video with the given format preset.

    `is_cancelled`, if given, is polled on every progress update; when it
    returns True the download is aborted cleanly.

    Returns a dict with keys: success, message, and (only when aborted via
    `is_cancelled`) cancelled=True.
    """
    _ensure_dir()
    ydl_opts = build_download_options(format_choice)

    def hook(d: dict) -> None:
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("Cancelled by user")
        if progress_hook:
            progress_hook(d)

    if progress_hook or is_cancelled:
        ydl_opts["progress_hooks"] = [hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return {"success": True, "message": "Download complete"}
    except DownloadCancelled:
        return {"success": False, "message": "Cancelled", "cancelled": True}
    except Exception as e:
        message = str(e) or type(e).__name__
        print(f"Error downloading video: {message}")
        return {"success": False, "message": message}
