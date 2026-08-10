"""YouTube search, info, and download helpers using yt-dlp."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlencode, urlunparse

import yt_dlp

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

    if format_choice == "best":
        return {
            "format": "bestvideo[height<=1080]+bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
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
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
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
            "format": "bestvideo[height<=720]+bestaudio/best",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mov",
                }
            ],
        }
    raise ValueError(f"Unknown format choice: {format_choice}")


def download_video(url: str, format_choice: str, progress_hook=None) -> bool:
    """Download a YouTube video with the given format preset."""
    _ensure_dir()
    ydl_opts = build_download_options(format_choice)
    if progress_hook:
        ydl_opts["progress_hooks"] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"Error downloading video: {e}")
        return False
