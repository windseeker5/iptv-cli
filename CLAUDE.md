# CLAUDE.md

Repository guidance for the IPTV project.

## Current application

`kdtv` (the Rust program in `rust/`, "KDTV CLI") is the interface: a
full-screen TUI plus scriptable commands (`--json`). The Python package
`iptv_tui/domain/` and `scripts/` are the engine behind it and behind JP: the
recorder, schedules, refresh jobs and JP's toolbox. The old Textual TUI
(`iptv.py`) has been removed.

The primary user workflows are:

- browse/search and play live TV, movies, and series;
- save favorites and generate an M3U playlist;
- record now or schedule a recording through systemd user timers;
- download VOD, series episodes, and YouTube media;
- browse completed media in the Library;
- restream a channel through NGINX-RTMP/HLS;
- inspect and control the optional Docker services.

## Architecture

- `rust/`: `kdtv`, the interface (see `rust/README.md`).
- `iptv_tui/domain/`: provider, media, recording, job, and infrastructure
  logic used by the scripts and JP.
- `scripts/record_scheduled.py`: FFmpeg recorder launched by systemd.
- `scripts/record_wrapper.sh`: activates the virtual environment for timers.
- `scripts/remux_downloads_for_tv.py`: optional on-demand MP4 remux tool.
- `data/`: runtime database, cache, logs, downloads, and fallback recordings.
- `nginx/html/`: web dashboard and HLS player used by the restream service.
- Docker: the shared `../../docker-compose.yml` (the DEV folder, two levels up) runs Caddy, KDTV, Jellyfin, NGINX-RTMP, tv-counter and Ollama (Samba was removed 2026-10-18); this folder holds their data.

## Important behavior

- Live recordings use the same systemd-backed path whether they begin now or
  later. Recording logs are written under `data/logs/`.
- VOD transfers are serialized because the provider may allow only one active
  connection.
- User state under `data/`, configured media paths, and Jellyfin directories
  must not be deleted or overwritten without explicit permission.
- `favorites_seed.json` is a credential-free export; `data/favorites.json` is
  the runtime source.
- Provider stream URLs contain credentials. Never print or expose them in logs.

## Setup and validation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd rust && cargo build --release && target/release/kdtv
```

Validate Python changes with:

```bash
python3 -m unittest iptv_tui.test_domain_smoke -v
python3 -m compileall iptv_tui
python3 -m py_compile scripts/*.py util.py
cd rust && cargo clippy --all-targets -- -D warnings && cargo test
```

Validate infrastructure changes with (run from the DEV folder, two levels up: `cd ../..`):

```bash
docker compose config -q
docker compose up -d --build
```

External tools used at runtime include `ffmpeg`, `ffprobe`, `mpv`, systemd,
and optionally Docker.
