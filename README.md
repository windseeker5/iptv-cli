# IPTV TUI

A terminal UI for browsing, searching, playing, downloading, and recording IPTV content — with an optional self-hosted stack (Jellyfin, restreaming, network share) to organize and serve it afterward.

## Tech stack

| Layer | Technology |
|---|---|
| UI | [Textual](https://textual.textualize.io/) (Python TUI framework) |
| Language | Python 3 |
| Local storage | SQLite (WAL mode) for the provider catalog + job/recording history |
| Playback | [mpv](https://mpv.io/) |
| Downloads | `wget`/`curl` (falls back to `requests` if neither is installed) |
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Video processing | `ffmpeg` / `ffprobe` — live recording, restream transcoding, TV-compatible conversion |
| Scheduled recordings | systemd user timers (`systemd-run`) |
| Self-hosted stack | Docker Compose: NGINX-RTMP, Jellyfin, Caddy (Cloudflare DNS plugin), Samba, a small viewer-counter service |
| Tests | `unittest` — domain smoke tests + Textual headless integration tests |

## Features

**Browse & play**
- Search and browse live channels, VOD, and series by category
- Live "now playing" EPG preview while browsing channels
- Play any stream or downloaded file via `mpv`
- Favorites list with an auto-generated M3U playlist

**Download & record**
- Download a single VOD/episode, or an entire series in one batch
- Optional **TV-compatible conversion**: re-encodes to H.264 / 1080p max / 30fps max / AAC — no 4K, no 60fps, no HEVC/AV1 — so cheap smart TVs can direct-play through Jellyfin without it transcoding live
- Record a live channel immediately, or **schedule** a recording for later via a systemd timer
- One unified **Downloads & Recordings** screen: live status for everything in flight, cancel any active job, and a type-to-confirm **Clear All** that wipes downloaded files, recordings, and tracking history in one step

**YouTube**
- Search, play, and download YouTube videos (best quality, 720p, or audio-only) via `yt-dlp`

**Restream & self-host**
- Restream any IPTV channel to your own NGINX-RTMP server (HLS/RTMP output)
- Manage the Docker stack (start/stop/status/logs) from inside the TUI
- Jellyfin for library playback, Caddy for HTTPS reverse-proxying, Samba for browsing recordings/downloads from other devices on the network

## Project layout

```
iptv.py                    entrypoint — launches the Textual app (iptv_tui/app.py)
scripts/
├── record_scheduled.py     standalone recorder invoked by scheduled systemd timers
└── record_wrapper.sh       bash wrapper (activates venv) used by systemd-run

iptv_tui/
├── app.py                  Textual App, screen registry, theme
├── domain/                 business logic — no UI dependencies
│   ├── iptv_provider.py    provider API client, catalog cache/sync
│   ├── downloads.py        VOD/series downloads
│   ├── recordings.py       scheduled recordings + systemd timers
│   ├── transcode.py        TV-compatible ffmpeg conversion
│   ├── youtube.py          yt-dlp search/download
│   ├── restream.py         NGINX-RTMP restreaming
│   ├── jobs.py             unified, disk-persisted job/history registry
│   ├── reset.py            "Clear All" — wipe files + history
│   ├── docker_ctl.py       docker-compose control
│   ├── favorites.py, db.py, config.py, actions.py
└── screens/                one Textual Screen per view
└── widgets/                shared widgets (header, status bar)

data/                       all runtime state (gitignored)
├── iptv.db                 SQLite catalog + job/recording history
├── favorites.json          your saved favorites
├── iptv.m3u                generated playlist
├── cache/                  raw provider API dumps (regenerated on each "Update Database")
├── logs/                   job history (jobs.json) + series-batch download logs
├── downloads/               downloaded VOD/series files
├── recordings/              recorded live files (falls back here if USB_RECORDS_PATH isn't writable)
└── youtube/                  downloaded YouTube files

docker-compose.yml          nginx-rtmp, jellyfin, caddy, samba, viewer-counter
infrastructure/             Caddyfile + Caddy image (Cloudflare DNS plugin) for HTTPS
nginx/                       NGINX-RTMP image, config, served web pages
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your IPTV provider credentials

python3 iptv.py
```

`iptv.py` auto-detects and activates `venv/` and switches to the script's own directory, so it can be run from anywhere.

External tools expected on `PATH`: `ffmpeg`/`ffprobe`, `mpv`, and `wget` or `curl` (optional — falls back to Python `requests`). `docker` + `docker-compose` are only needed if you use the self-hosted stack; scheduled recordings need `systemd` (Linux).

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `IPTV_SERVER_URL`, `IPTV_USERNAME`, `IPTV_PASSWORD` | required — your IPTV provider |
| `EPG_SERVER_URL` | optional override if EPG is served from a different host |
| `USB_RECORDS_PATH`, `USB_MOVIES_PATH`, `USB_MUSIC_PATH`, `USB_PHOTOS_PATH` | mount points shared with Jellyfin/Samba and used for recordings |
| `NGINX_RTMP_PORT`, `NGINX_HTTP_PORT`, `NGINX_ADMIN_PORT` | restream server ports |
| `JELLYFIN_PUBLISHED_SERVER_URL`, `JELLYFIN_DOMAIN`, `CADDY_DOMAIN`, `LETSENCRYPT_EMAIL`, `CLOUDFLARE_API_TOKEN` | HTTPS reverse-proxy for Jellyfin via Caddy |
| `SAMBA_*_PORT`, `TZ` | network share configuration |

See `.env.example` for the full list and defaults.

## Self-hosted stack

```bash
docker-compose up -d --build      # start everything
docker-compose logs -f <service>  # nginx-rtmp | jellyfin | caddy | samba | viewer-counter
docker-compose down               # stop everything
```

Container status, start/stop, and logs are also available from the TUI's **Settings** screen.

## Testing

```bash
python3 -m unittest iptv_tui.test_domain_smoke iptv_tui.test_integration iptv_tui.test_app_smoke -v
```

## Status

`iptv.py` is the Textual TUI (`iptv_tui/`) — this is the application. The previous `simple-term-menu` CLI it replaced has been removed; see git history if you ever need it back.
