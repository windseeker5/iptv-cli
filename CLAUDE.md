# CLAUDE.md

Repository guidance for KDTV CLI.

## Current application

`kdtv` (the Rust program in `rust/`) is the whole application: the full-screen
TUI, scriptable commands (`--json`), the recorder, the timers' jobs (refresh,
pre-flight, Maxime's schedule) and JP's toolbox and runner for the OMT site. The
Python engine and the Textual TUI it replaced are in git history only.

## Architecture

- `rust/`: `kdtv` (see `rust/README.md` for the file map).
- `systemd/`: the timers and JP's runner, all running `~/.local/bin/kdtv`.
  Recording schedules write their own `kdcli-rec-<id>` units.
- `data/`: runtime database, schedules, recording diary (`runs/`), logs,
  downloads, fallback recordings, JP's state (`agent/`).
- `nginx/`: the NGINX-RTMP image and the pages it serves for the restream.
- Docker: the shared `../../docker-compose.yml` (the DEV folder, two levels up)
  runs Caddy, Jellyfin, NGINX-RTMP, tv-counter, OMT and Ollama; the OMT
  container reaches JP's runner through `data/agent/run/runner.sock`.

## Important behavior

- The provider allows one connection: recordings, restreams, playing and
  downloads must not overlap. Schedules that overlap are refused.
- Every recording, now or scheduled, runs `kdtv` inside a systemd unit
  (`kdcli-rec-*` or `kdcli-now-*`) and writes a diary in `data/runs/`.
- After installing `kdtv` somewhere new, run `kdtv schedule repair`.
- User state under `data/`, configured media paths, and Jellyfin directories
  must not be deleted or overwritten without explicit permission.
- `favorites_seed.json` is a credential-free export; `data/favorites.json` is
  the runtime source.
- Provider stream URLs contain credentials. Never print or expose them in logs;
  JP's tools redact secrets from everything they return.

## Setup and validation

```bash
cp .env.example .env
cd rust && cargo build --release && install -Dm755 target/release/kdtv ~/.local/bin/kdtv
cargo clippy --all-targets -- -D warnings && cargo test
```

After rebuilding: `systemctl --user restart kdcli-agent-runner` so JP uses the new
program (timers pick it up on their next run).

Validate infrastructure changes with (run from the DEV folder, two levels up: `cd ../..`):

```bash
docker compose config -q
docker compose up -d --build
```

External tools used at runtime: `ffmpeg`, `ffprobe`, `curl`, `mpv`, `yt-dlp`,
systemd, `journalctl`, and optionally Docker and Claude Code (for JP).
