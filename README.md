# KDTV CLI

`kdtv` is one Rust program for live TV, movies, series and YouTube from an IPTV
provider: a full-screen terminal interface, scriptable commands (`--json`), the
recorder, the timers' jobs, and JP's toolbox for the OMT site. An optional
self-hosted stack (Jellyfin, restreaming) serves what it records.

## What it does

**Browse & play**: search live channels, movies and series; what's on now and
next; play in `mpv`; favorites.

**Record**: record now or on a schedule (once or every week), until the show
ends. Recordings survive a busy or dropping provider: the recorder retries until
the planned end and joins the pieces. A schedule can send the episode straight to
Jellyfin (converted to MP4, named `Show - S2026E042 - 2026-10-21.mp4`, library
rescanned) and pings Discord when done. A pre-flight check looks at every
recording an hour before it starts and pings Discord only when something is wrong.

**Download**: movies, an episode, a season or a whole series, one at a time (the
provider allows one connection); YouTube video or audio through `yt-dlp`.

**Restream** a channel to the family's live page (NGINX-RTMP, or `kdtv web`).

**JP**: the OMT site's chat helper runs on `kdtv jp-runner` and `kdtv jp-mcp`. It
looks things up, proposes recordings and restreams (nothing happens until someone
taps Confirm), and, for the owner, reads the recording diary, checks files, probes
the provider and reads logs.

## Setup

Needs `ffmpeg`/`ffprobe`, `curl`, `mpv`, `yt-dlp` (YouTube only), systemd for
recordings and timers, and Rust to build.

```bash
git clone https://github.com/windseeker5/iptv-cli.git && cd iptv-cli
cp .env.example .env                  # put in your provider login
cd rust && cargo build --release
install -Dm755 target/release/kdtv ~/.local/bin/kdtv
mkdir -p ~/.config/kdtv && cp kdtv.example.toml ~/.config/kdtv/kdtv.toml   # set cli_dir
kdtv refresh --everything             # channels, guide, movies, series
kdtv check                            # what's ready, what's missing
kdtv                                  # the full-screen interface
```

For recordings while logged out: `sudo loginctl enable-linger $USER`. After
moving or reinstalling `kdtv`, `kdtv schedule repair` points every recording
timer at it again.

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `IPTV_SERVER_URL`, `IPTV_USERNAME`, `IPTV_PASSWORD` | required: your IPTV provider |
| `USB_RECORDS_PATH` | where recordings go (falls back to `data/recordings` when it isn't writable) |
| `TVSHOW_LIBRARY_ROOT` | Jellyfin's TV folder, one sub-folder per show (or `NOOVO_LIBRARY_DIR`, a show folder inside it) |
| `JELLYFIN_PUBLISHED_SERVER_URL`, `JELLYFIN_API_KEY` | library rescans (or `JELLYFIN_USERNAME` / `JELLYFIN_PASSWORD`) |
| `DISCORD_WEBHOOK_URL` | recording results and pre-flight warnings |

The Docker stack in the DEV folder reads a few more (ports, domains); see
`.env.example`.

## Background jobs (`systemd/`)

| Unit | Runs |
|---|---|
| `kdcli-rec-<id>.timer` | `kdtv run-schedule <id>`, one per schedule (written by `kdtv schedule add` and JP) |
| `kdcli-refresh-channels.timer` | `kdtv refresh`, hourly |
| `kdcli-refresh-guide.timer` | `kdtv refresh --guide`, twice a day |
| `kdcli-preflight.timer` | `kdtv preflight`, every 10 minutes |
| `kdcli-refresh-maxime.timer` | `kdtv maxime --refresh`, twice a day (for JP) |
| `kdcli-agent-runner.service` | `kdtv jp-runner`, JP's bridge to the OMT site |

Install with `ln -s "$PWD"/systemd/kdcli-* ~/.config/systemd/user/`, then
`systemctl --user daemon-reload` and `systemctl --user enable --now` each timer.

## Data (`data/`, never committed)

`iptv.db` (catalog and guide), `schedules/` (one JSON per schedule), `runs/` (the
recording diary, one JSON per recording), `logs/`, `downloads/`, `recordings/`
(fallback), `agent/` (JP's pending actions, history and socket), `maxime/`.

## Self-hosted stack

```bash
# from the DEV folder (cd ../..)
docker compose up -d
docker compose logs -f <service>     # caddy | jellyfin | nginx-rtmp | tv-counter | omt
```

## Testing

```bash
cd rust && cargo clippy --all-targets -- -D warnings && cargo test
```

More in `rust/README.md`.
