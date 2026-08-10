# New Rework IPTV Plan

Branch: `textual-poc`

Status: **completed**

Goal: keep Python, extract domain logic from `iptv.py`, and replace `simple-term-menu` with a minimal `textual` TUI. No flashy UI — black background, white/cyan text, clean flow.

## Constraints

- Existing `iptv.py` must keep working until cutover.
- New code lives in `new_iptv/` package.
- Style is intentionally minimal: no animations, no gradients, no rounded windows.
- Use `python-dotenv`, `rich`/`textual`, `requests`, `yt-dlp`, `sqlite3`, `subprocess`.

## Final layout

```
iptv/
├── iptv.py                  # new Textual TUI entrypoint
├── iptv_legacy.py           # old single-file simple-term-menu implementation
├── record_scheduled.py      # standalone recorder
├── record_wrapper.sh        # venv wrapper
├── docker-compose.yml
├── nginx/
├── plan/
│   └── new_rework_IPTV_plan.md
├── new_iptv/                # new modular app
│   ├── __init__.py
│   ├── app.py               # textual App entrypoint
│   ├── styles.tcss          # minimal theme
│   ├── screens/
│   │   ├── __init__.py
│   │   ├── main_menu.py
│   │   ├── search.py
│   │   ├── results.py
│   │   ├── player_actions.py
│   │   ├── favorites.py
│   │   ├── category_browser.py
│   │   ├── scheduled_recordings.py
│   │   ├── background_downloads.py
│   │   ├── series_episodes.py
│   │   ├── info.py
│   │   ├── youtube.py
│   │   ├── container_status.py
│   │   └── message.py
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── header.py
│   │   └── status_bar.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── iptv_provider.py
│   │   ├── favorites.py
│   │   ├── recordings.py
│   │   ├── restream.py
│   │   ├── downloads.py
│   │   ├── docker_ctl.py
│   │   ├── youtube.py
│   │   └── actions.py
│   └── test_app_smoke.py
└── util.py                  # image/logo helpers
```

## Phase 0 — Skeleton ✅

- Created `new_iptv/` package directories.
- Added `styles.tcss` with black/white/cyan theme.
- Created minimal `new_iptv/app.py` and `run_new_iptv.py` launcher.
- Added headless smoke tests.

## Phase 1 — Domain extraction ✅

- Extracted `config.py`, `db.py`, `iptv_provider.py`, `favorites.py`, `recordings.py`, `restream.py`, `downloads.py`, `docker_ctl.py`, `youtube.py`.
- Added `db.connection()` context manager to prevent SQLite connection leaks.

## Phase 2 — Textual screens ✅

- Built main menu, search, results with EPG preview, player actions, favorites, category browser, container status, message screen.
- Wired screens through `app.py`.

## Phase 3 — Feature parity ✅

- Search → results → play/restream/record/download/favorite/info.
- Favorites, category browser, container controls.
- Scheduled recordings, background downloads, series episodes, info screens, YouTube tool.
- Real action execution through `domain/actions.py`.

## Phase 4 — Cutover ✅

- Renamed `iptv.py` → `iptv_legacy.py`.
- Renamed `run_new_iptv.py` → `iptv.py`.
- Updated `AGENTS.md` to reflect new architecture.

## Validation

- `python3 -m py_compile iptv.py record_scheduled.py util.py test_tty.py`
- `python3 -m compileall new_iptv`
- `python3 -m unittest new_iptv.test_app_smoke -v`
- `docker-compose config -q`
