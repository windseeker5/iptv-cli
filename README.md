# IPTV TUI

A terminal UI for browsing, searching, playing, downloading, and recording IPTV content — with an optional self-hosted stack (Jellyfin, restreaming, network share) to organize and serve it afterward.

## Tech stack

| Layer | Technology |
|---|---|
| UI | `kdtv`, a Rust TUI (ratatui) and command line — see `rust/README.md` |
| Language | Python 3 |
| Local storage | SQLite (WAL mode) for the provider catalog + job/recording history |
| Playback | [mpv](https://mpv.io/) |
| Downloads | `wget`/`curl` (falls back to `requests` if neither is installed) |
| YouTube | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Video processing | `ffmpeg` / `ffprobe` — live recording, restream transcoding |
| Scheduled recordings | systemd user timers (`systemd-run`) |
| Self-hosted stack | One Docker Compose file in the DEV folder two levels up (`../../docker-compose.yml`): Caddy, KDTV, Jellyfin, NGINX-RTMP, Samba, tv-counter, Ollama (off by default) |
| Tests | `cargo test` for `kdtv`; `unittest` domain smoke tests for the Python engine |

## Features

**Browse & play**
- Search and browse live channels, VOD, and series by category
- Live "now playing" EPG preview while browsing channels
- Play any stream or downloaded file via `mpv`
- Favorites list with an auto-generated M3U playlist

**Download & record**
- Download a single VOD/episode, or an entire series in one batch — downloads are queued and run one at a time (the provider only allows one concurrent connection)
- Use one recording form to start now or **schedule** a recording, choose its duration, and optionally record until the current program ends
- **Recording & Download Queue** shows queued/running work, supports cancellation, and opens recording logs
- **My Videos** browses completed media, plays or deletes files, and remuxes files for TV playback
- A type-to-confirm **Clear All** wipes downloaded files, recordings, and tracking history in one step

**YouTube**
- Search, play, and download YouTube videos (best quality, 720p, or audio-only) via `yt-dlp`

**Restream & self-host**
- Restream any IPTV channel to your own NGINX-RTMP server (HLS/RTMP output)
- Manage the Docker stack (start/stop/status/logs) from inside the TUI
- Jellyfin for library playback, Caddy for HTTPS reverse-proxying, Samba for browsing recordings/downloads from other devices on the network

## Project layout

```
rust/                      kdtv — the interface (TUI + commands)
scripts/
├── record_scheduled.py     standalone recorder invoked by scheduled systemd timers
└── record_wrapper.sh       bash wrapper (activates venv) used by systemd-run

iptv_tui/
├── domain/                 the Python engine (used by scripts/ and JP) — no UI
│   ├── iptv_provider.py    provider API client, catalog cache/sync
│   ├── downloads.py        VOD/series downloads
│   ├── recordings.py       immediate/scheduled recordings + systemd timers
│   ├── library.py          completed media discovery and safe deletion
│   ├── youtube.py          yt-dlp search/download
│   ├── restream.py         NGINX-RTMP restreaming
│   ├── jobs.py             unified, disk-persisted job/history registry
│   ├── reset.py            "Clear All" — wipe files + history
│   ├── docker_ctl.py       docker-compose control
│   └── favorites.py, db.py, config.py, actions.py
└── test_domain_smoke.py

data/                       all runtime state (gitignored)
├── iptv.db                 SQLite catalog + job/recording history
├── favorites.json          your saved favorites
├── iptv.m3u                generated playlist
├── cache/                  raw provider API dumps (regenerated on each "Update Database")
├── logs/                   job history plus recording/restream diagnostics
├── downloads/               downloaded VOD/series files
├── recordings/              recorded live files (falls back here if USB_RECORDS_PATH isn't writable)
└── youtube/                  downloaded YouTube files

(docker-compose.yml and the Caddyfile live in DEV/, two levels up; Jellyfin's data is in DEV/jellyfin/)
nginx/                       NGINX-RTMP image, config, served web pages
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env with your IPTV provider credentials

rust/target/release/kdtv
```

`kdtv` finds this folder through `kdtv.toml` (see `rust/kdtv.example.toml`), so it can be run from anywhere.

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
# run these from the DEV folder (cd ../..)
docker compose up -d                 # start everything (Ollama stays off)
docker compose logs -f <service>     # caddy | kdtv | jellyfin | nginx-rtmp | samba | tv-counter
docker compose pull && docker compose up -d --build --pull always   # update to latest versions
docker compose down                  # stop everything
```

Container status, start/stop, and logs are also available from the TUI's **Settings** screen.

## Testing

```bash
python3 -m unittest iptv_tui.test_domain_smoke -v
cd rust && cargo test
```

## Status

`kdtv` (`rust/`) is the application. The Textual TUI (`iptv.py`) it replaced has been removed; see git history if you ever need it back.
