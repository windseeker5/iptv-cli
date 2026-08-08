# AGENTS.md

Operational guidance for coding agents working in `/home/kdresdell/Documents/DEV/iptv`.

## Project Snapshot
- Python CLI app for IPTV browsing, restreaming, downloads, and container control.
- Main file is `iptv.py` with a large stateful `IPTVMenuManager` class.
- `record_scheduled.py` is a standalone recorder entrypoint (automation/systemd style).
- `util.py` contains image/logo and helper workflows.
- `docker-compose.yml` manages `nginx-rtmp`, `jellyfin`, and `samba` services.
- Primary persistent state is user data in `data/`.

## Rule Sources Checked
- Searched for Cursor rules in `.cursor/rules/` and `.cursorrules`: not present.
- Searched for Copilot rules in `.github/copilot-instructions.md`: not present.
- Repo-level architecture context exists in `CLAUDE.md`; consult it for deeper behavior.

## Important Paths
- `iptv.py`: main interactive CLI, DB logic, IPTV API handling, FFmpeg orchestration.
- `record_scheduled.py`: direct scheduled recording script.
- `util.py`: utility helpers.
- `test_tty.py`: terminal smoke/diagnostic script (not a formal unit test suite).
- `data/`: SQLite DB, logs, JSON payloads, generated playlists, downloaded media.
- `nginx/`: NGINX RTMP Docker context and web assets.
- `nginx/html/`: web-served artifacts (including generated playlist copies).
- `README.md`: restreaming and container usage details.

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
```

## Docker Commands
```bash
docker-compose config -q
docker-compose up -d --build
docker-compose up -d --build nginx-rtmp
docker-compose logs -f nginx-rtmp
docker-compose down
```

## Lint / Static Validation
No repo config is present for `ruff`, `flake8`, `black`, `isort`, `mypy`, `pylint`, `tox`, or `nox`.
Also no `pyproject.toml`, `setup.cfg`, `pytest.ini`, or `Makefile` is checked in.
Use Python syntax compilation as the safest default validation:
```bash
python3 -m py_compile iptv.py
python3 -m py_compile iptv.py record_scheduled.py util.py test_tty.py
python3 -m compileall iptv.py record_scheduled.py util.py test_tty.py
```
If Docker files are edited, also run:
```bash
docker-compose config -q
```

## Test Commands (Including Single-Test Guidance)
Current state:
- No formal unit/integration test suite is checked in.
- `test_tty.py` is a smoke/diagnostic script and may require an interactive TTY.
Practical commands today:
```bash
python3 test_tty.py
python3 -m py_compile record_scheduled.py
```
If/when pytest tests are added later, use these conventions:
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
- Do not delete or overwrite persistent data unless task explicitly requires it.
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
