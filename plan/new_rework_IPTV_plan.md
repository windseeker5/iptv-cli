# New Rework IPTV Plan

Branch: `textual-poc`

Goal: keep Python, extract domain logic from `iptv.py`, and replace `simple-term-menu` with a minimal `textual` TUI. No flashy UI — black background, white/cyan text, clean flow.

## Constraints

- Existing `iptv.py` must keep working until cutover.
- New code lives in `new_iptv/` package.
- Style is intentionally minimal: no animations, no gradients, no rounded windows.
- Use `python-dotenv`, `rich`/`textual`, `requests`, `yt-dlp`, `sqlite3`, `subprocess`.

## Target layout

```
iptv/
├── iptv.py                  # legacy app (untouched during build)
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
│   │   └── container_status.py
│   ├── widgets/
│   │   ├── __init__.py
│   │   ├── header.py
│   │   ├── status_bar.py
│   │   └── simple_list.py
│   └── domain/
│       ├── __init__.py
│       ├── config.py
│       ├── db.py
│       ├── iptv_provider.py
│       ├── favorites.py
│       ├── recordings.py
│       ├── restream.py
│       ├── downloads.py
│       ├── docker_ctl.py
│       └── youtube.py
└── run_new_iptv.py          # launcher for the new app
```

## Phase 0 — Skeleton

1. Create `new_iptv/` package directories.
2. Add `styles.tcss` with black/white/cyan theme.
3. Create minimal `new_iptv/app.py` and `run_new_iptv.py` launcher.
4. Validate: `python3 run_new_iptv.py` opens a window and exits cleanly.

## Phase 1 — Domain extraction (no UI yet)

1. Extract `config.py` from `iptv.py` env loading.
2. Extract `db.py` with connection helper and migrations.
3. Extract `iptv_provider.py` for search/live/vod/series/EPG.
4. Extract `favorites.py` for load/save and M3U generation.
5. Extract `recordings.py` for scheduled recordings table.
6. Extract `restream.py` for FFmpeg start/stop/ PID management.
7. Extract `downloads.py` for VOD/series background downloads.
8. Extract `docker_ctl.py` for docker-compose orchestration.
9. Extract `youtube.py` for yt-dlp search/info/download.
10. Validate each module with `python3 -m py_compile` and light manual checks.

## Phase 2 — Textual screens

1. `screens/main_menu.py` — main menu with status bar.
2. `screens/search.py` — single search input.
3. `screens/results.py` — unified live/VOD/series list with key actions.
4. `screens/player_actions.py` — context menu for selected item.
5. `screens/container_status.py` — service list and logs.
6. Wire screens together through `app.py` push/pop.

## Phase 3 — Feature parity, one flow at a time

1. Main menu → Search → Results → Play.
2. Favorites → Play/Restream.
3. Browse categories → Live channels → Play.
4. Container status / start / stop / logs.
5. Scheduled recordings.
6. Background downloads.
7. YouTube tool.

## Phase 4 — Cutover

1. Verify all daily flows work in `run_new_iptv.py`.
2. Rename `iptv.py` → `iptv_legacy.py`.
3. Rename `run_new_iptv.py` → `iptv.py`.
4. Keep `record_scheduled.py`, `record_wrapper.sh`, Docker files.
5. Update `AGENTS.md` and docs.

## Validation rules

- Run `python3 -m py_compile <edited_file>.py` after every file edit.
- Run `docker-compose config -q` if Docker files change.
- Keep `iptv.py` legacy functional until explicit cutover step.

## Visual target

- Black background, white text, cyan accents for highlights/borders.
- No animations, no rounded panels, no gradients.
- Clear footer showing available keys on each screen.
