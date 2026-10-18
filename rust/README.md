# KDTV CLI (`kdtv`)

One program for live TV, movies, series, YouTube, recordings, restreams and
downloads. `kdtv` is the brain and the screen; the proven Python recorder
(`scripts/record_scheduled.py`) still does the recording itself.

## Run it

```bash
cd rust && cargo build --release          # the program: target/release/kdtv
cp kdtv.example.toml ~/.config/kdtv/kdtv.toml   # then set cli_dir
target/release/kdtv                       # the full-screen interface
target/release/kdtv --help                # every command; add --json for scripts/JP
```

## The screen

A menu on the left, the list in the middle, details and action buttons on the
right, and a status bar (REC, RESTREAMING, downloads) at the bottom. `?` shows
every key; the bottom-right corner always shows the keys that work right now.

| # | Section | What | Keys |
| --- | --- | --- | --- |
| 1 | Search | live, movies, series and YouTube in one box | type · ↵ open |
| 2 | Live | favorites, what's on, time left; game channels by their game | ↵ watch · r record · n next · s restream · x stop restream |
| 3 | Movies | categories or search | ↵ watch · d download |
| 4 | Series | categories, seasons, episodes | ↵ watch · [ ] season · d / s / D download episode, season, all |
| 5 | YouTube | search, watch, download | ↵ watch · d video · a audio |
| 6 | Recordings | now, schedules, recent | x stop · space pause · d delete |
| 7 | Downloads | the queue, one at a time | x cancel |
| 8 | Library | every file on disk | ↵ play |
| 9 | Account | provider login, subscription, lists | u update list · g update guide · p password · e edit |
| 0 | Status | services, folders, today's log | |

Other commands worth knowing: `kdtv refresh [--guide | --everything]`,
`kdtv check`, `kdtv web`, `kdtv youtube <words>`, `kdtv download series <id> --season 3`.

Your provider allows **one connection**. Playing, recording or restreaming while
something else holds it asks first; a schedule that overlaps another is refused;
"record now" ends before the next scheduled recording; provider downloads pause
by themselves while a recording or restream runs, then resume.

No Nerd Font on a terminal (a Pi console)? Set `icons = false` in `kdtv.toml`.

## On the road: a laptop or a Raspberry Pi

`kdtv` runs on its own there: no Docker, no Jellyfin, no NGINX. It records to a
local folder and restreams by itself (a phone or TV on the same Wi-Fi opens the
address it shows).

```bash
# 1. What it needs (Raspberry Pi OS / Debian names)
sudo apt install git ffmpeg curl mpv python3-venv
pipx install yt-dlp                     # or: sudo apt install yt-dlp
curl https://sh.rustup.rs -sSf | sh     # Rust, to build kdtv on the Pi itself

# 2. The code, the Python recorder, your login
git clone <this repository> ~/kdtv/cli && cd ~/kdtv/cli
git checkout feature/rust-refactor
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env                    # then put in IPTV_SERVER_URL / USERNAME / PASSWORD

# 3. Build kdtv (a few minutes on a Pi 4 or 5)
cd rust && cargo build --release && cd ..
mkdir -p ~/.config/kdtv && cp rust/kdtv.travel.example.toml ~/.config/kdtv/kdtv.toml

# 4. Download your lists, check, go
rust/target/release/kdtv refresh --everything
rust/target/release/kdtv check
rust/target/release/kdtv
```

`kdtv check` lists what's ready and what's missing. For timers to record while
you're logged out: `sudo loginctl enable-linger $USER`. No Nerd Font on a Pi
console: `icons = false` in `kdtv.toml`.

## Retiring the NGINX container

`kdtv web` does everything NGINX does for the restream: it serves `/hls`, the
`/live` pages and `iptv.m3u` from `nginx/html`, on the same port (8080), with the
same headers, and writes the same `hls_access.log` lines OMT and tv-counter read
to count viewers. So Caddy, OMT and the Android app don't change.

The switch waits until JP restreams through `kdtv`: today JP's Python toolbox still
pushes RTMP to NGINX. Then:

1. `restream_via = "kdtv"` and `hls_dir = "../cli/nginx/hls-kdtv"` in `kdtv.toml`
2. `docker compose stop nginx-rtmp` (DEV folder), then remove its block from
   `docker-compose.yml` (keep a backup)
3. install and start `systemd/kdtv-web.service`
4. open https://live.dresdell.com and start a restream to check

## How the code is laid out

| File | Job |
| --- | --- |
| `main.rs` | the commands (`kdtv record`, `kdtv schedule add`, `kdtv youtube`, …) |
| `config.rs` | reads `kdtv.toml` and the CLI's `.env` |
| `db.rs` | the channel/movie/series database; game-channel names |
| `record.rs`, `schedules.rs` | record now, stop, schedules (same files and timers as the Python side) |
| `restream.rs` | restream unit `kd-restream` (FFmpeg → NGINX-RTMP) |
| `downloads.rs` | download queue + worker unit `kd-downloads` (curl, yt-dlp) |
| `youtube.rs`, `provider.rs`, `refresh.rs` | YouTube search, series episodes, channel list and guide downloads |
| `web.rs`, `player.html` | the restream web server (replaces NGINX) and its player page |
| `check.rs` | `kdtv check`: is this machine ready? |
| `library.rs`, `runs.rs`, `status.rs`, `favorites.rs` | reading what's on disk |
| `systemd.rs`, `play.rs`, `log.rs` | helpers: units, mpv, the JSON event log |
| `tui/` | the screen: one file per section, `modal.rs` for pop-ups, `theme.rs` for the look |

Everything is logged as one JSON line in `data/logs/kd/kd-YYYY-MM-DD.jsonl`, with
the provider login removed.
