# CLAUDE.md

Repository guidance for the IPTV project.

## Current application

`iptv.py` launches a modular Textual terminal interface. The old
`simple-term-menu` application has been removed.

The primary user workflows are:

- browse/search and play live TV, movies, and series;
- save favorites and generate an M3U playlist;
- record now or schedule a recording through systemd user timers;
- download VOD, series episodes, and YouTube media;
- browse completed media in the Library;
- restream a channel through NGINX-RTMP/HLS;
- inspect and control the optional Docker services.

## Architecture

- `iptv_tui/screens/`: Textual screens and user interaction.
- `iptv_tui/domain/`: UI-independent provider, media, recording, job, and
  infrastructure logic.
- `scripts/record_scheduled.py`: FFmpeg recorder launched by systemd.
- `scripts/record_wrapper.sh`: activates the virtual environment for timers.
- `scripts/remux_downloads_for_tv.py`: optional on-demand MP4 remux tool.
- `data/`: runtime database, cache, logs, downloads, and fallback recordings.
- `nginx/html/`: web dashboard and HLS player used by the restream service.
- `docker-compose.yml`: NGINX-RTMP, Jellyfin, Caddy, Samba, and viewer counter.

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
python3 iptv.py
```

Validate Python changes with:

```bash
python3 -m unittest iptv_tui.test_domain_smoke iptv_tui.test_integration iptv_tui.test_app_smoke -v
python3 -m compileall iptv_tui
python3 -m py_compile iptv.py scripts/*.py util.py
```

Validate infrastructure changes with:

```bash
docker compose config -q
docker compose up -d --build
```

External tools used at runtime include `ffmpeg`, `ffprobe`, `mpv`, systemd,
and optionally Docker.
