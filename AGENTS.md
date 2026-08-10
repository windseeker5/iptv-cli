# AGENTS.md

Operational guidance for coding agents working in `/home/kdresdell/Documents/DEV/iptv`.

## Project Snapshot

- Python CLI app for IPTV browsing, restreaming, downloads, and container control.
- Main file is `iptv.py` with a large stateful `IPTVMenuManager` class.
- `record_scheduled.py` is a standalone recorder entrypoint (automation/systemd style).
- `util.py` contains image/logo helper workflows.
- `docker-compose.yml` manages `nginx-rtmp`, `jellyfin`, `caddy`, `viewer-counter`, and `samba` services.
- Primary persistent state is user data in `data/`; some artifacts are mirrored to `nginx/html/` for web serving.

## Rule Sources Checked

- Cursor rules in `.cursor/rules/` and `.cursorrules`: not present.
- Copilot rules in `.github/copilot-instructions.md`: not present.
- Repo-level architecture context exists in `CLAUDE.md`; consult it for deeper behavior.

## Environment Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Required env vars for normal app startup:

- `IPTV_SERVER_URL`
- `IPTV_USERNAME`
- `IPTV_PASSWORD`

Common external dependencies:

- `ffmpeg`
- `docker`
- `docker-compose`
- `mpv`

## Build / Run Commands

There is no separate build system; execution is script + Docker driven.

```bash
python3 iptv.py
python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /tmp/test.ts
python3 util.py
python3 test_tty.py          # TTY smoke test; may need an interactive terminal
```

`iptv.py` auto-detects and activates `venv/` if present and changes the working directory to the script's directory before running.
`record_wrapper.sh` is the Bash wrapper for scheduled recordings (activates `venv` then runs `record_scheduled.py`).

## Docker Commands

```bash
docker-compose config -q                          # validate compose file
docker-compose up -d --build                      # start all services
docker-compose up -d --build nginx-rtmp           # rebuild only nginx-rtmp
docker-compose logs -f nginx-rtmp
docker-compose down
```

Key service defaults:

- NGINX RTMP input: `rtmp://localhost:1935/live/<key>`
- NGINX HLS output: `http://localhost:8080/hls/<key>.m3u8`
- NGINX stats/admin: `http://localhost:8080/stat`, `http://localhost:8081`
- Jellyfin: `http://localhost:8096`
- Caddy proxy depends on `viewer-counter` and `jellyfin`

## Lint / Static Validation

No repo config is present for `ruff`, `flake8`, `black`, `isort`, `mypy`, `pylint`, `tox`, or `nox`.
No `pyproject.toml`, `setup.cfg`, `pytest.ini`, or `Makefile` is checked in.
Use Python syntax compilation as the safest default validation:

```bash
python3 -m py_compile iptv.py record_scheduled.py util.py test_tty.py
python3 -m compileall iptv.py record_scheduled.py util.py test_tty.py
```

If Docker files are edited, also run:

```bash
docker-compose config -q
```

## Test Commands

- No formal unit/integration test suite is checked in.
- `test_tty.py` is a smoke/diagnostic script and may require an interactive TTY.

```bash
python3 test_tty.py
python3 -m py_compile record_scheduled.py
```

If pytest tests are added later, prefer:

```bash
pytest tests/test_some_module.py
pytest tests/test_some_module.py::test_specific_behavior
pytest -k "specific_behavior"
```

## Code Style And Change Scope

- Prefer the smallest correct change; do not refactor broadly unless requested.
- `iptv.py` is stateful and menu-coupled; preserve menu flow and side effects.
- Keep 4-space indentation and readable formatting.
- Follow surrounding style over introducing new patterns.
- Add docstrings for new non-trivial helpers and entrypoint-facing functions.
- Avoid comments that only restate obvious code.

## Imports

- Use standard grouping: standard library, third-party, local modules.
- Keep imports used; remove unused imports in touched files.
- Prefer module-level imports unless dependency is optional/platform-specific.
- Function-local imports are acceptable for optional tools and graceful fallback paths.

## Types

- Codebase is mostly dynamic; avoid forced large typing retrofits.
- Add light type hints for new helper functions where they improve clarity.
- Keep annotations simple (`str`, `int`, `list`, `dict`) unless stronger typing is useful.
- Match local file conventions (`record_scheduled.py` is more typed than `iptv.py`).

## Naming Conventions

- `snake_case`: functions, methods, variables, filenames.
- `PascalCase`: classes.
- `UPPER_CASE`: module-level constants.
- Use descriptive, action-oriented names aligned with current code vocabulary.

## Error Handling

- Prefer targeted exceptions (`except ValueError`, etc.) in new code.
- Use broad catches only where CLI resilience is required.
- On recoverable errors, emit a clear message and return a safe value (`None`/`False`).
- Reserve `sys.exit(...)` for true script-entry fatal conditions.
- Preserve graceful degradation when optional binaries/services are unavailable.

## Logging And User Output

- In `iptv.py`, prefer existing `rich`/`console.print(...)` patterns.
- In standalone scripts, prefer the `logging` module.
- Keep messages concise, actionable, and user-focused.
- When subprocess work fails, surface the relevant stderr/context.

## Data, SQL, And Subprocess Safety

- Treat `.env`, `data/`, playlists, JSON dumps, and recordings as user state.
- Do not delete or overwrite persistent data unless the task explicitly requires it.
- Keep new persistent artifacts under `data/` when feasible.
- Remember some artifacts are mirrored between `data/` and `nginx/html/`.
- Use parameterized SQL queries; never build SQL with string interpolation.
- Use subprocess argument lists; avoid `shell=True` unless unavoidable.

## Agent Workflow Expectations

- Read surrounding functions before editing; many behaviors are interconnected.
- Do not perform unrelated cleanups in focused task changes.
- If you add env vars or new user workflows, update `.env.example` and/or docs.
- Validate the smallest relevant surface before finishing.
- Minimum validation for Python edits: `python3 -m py_compile <edited_file>.py`.
