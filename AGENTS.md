# AGENTS.md

Guidance for coding agents working in `/home/kdresdell/Documents/DEV/iptv`.

## Repo Summary
This repo is a Python IPTV CLI plus Docker-based streaming infrastructure.
`iptv.py` is the main application and contains the large `IPTVMenuManager` class.
It handles menus, SQLite access, IPTV API fetches, playlist generation,
YouTube actions, FFmpeg restreaming, and Docker orchestration.
`record_scheduled.py` is a standalone recorder CLI used by schedulers/systemd.
`util.py` contains image/logo helpers.
`test_tty.py` is a terminal diagnostic script, not a real unit test.
`docker-compose.yml` defines `nginx-rtmp`, `jellyfin`, and `samba` services.
Persistent state lives under `data/`.

## Key Paths
`data/` holds the SQLite database, logs, JSON downloads, and generated state.
`nginx/` contains the RTMP container build context and served web assets.
`nginx/html/` is part of the app's web-facing output surface.
`jellyfin/` contains local Jellyfin config/cache directories.
`downloads/` is used for downloaded media artifacts.
`.env.example` documents supported environment variables.
`README.md` and `CLAUDE.md` contain the best high-level product context.

## Existing Agent Rules
There is a repo-level `CLAUDE.md`; use it for architecture context.
There was no existing repo `AGENTS.md` before this file.
I did not find `.cursor/rules/`, `.cursorrules`,
or `.github/copilot-instructions.md`.
There are no checked-in Cursor or Copilot rules to merge here.

## Environment Setup
Create a venv with `python3 -m venv venv`.
Activate it with `source venv/bin/activate`.
Install dependencies with `pip install -r requirements.txt`.
Create local config with `cp .env.example .env`.
Required `.env` variables for normal app startup are:
`IPTV_SERVER_URL`, `IPTV_USERNAME`, and `IPTV_PASSWORD`.
Common external tools used by the repo are `ffmpeg`, `docker`,
`docker-compose`, and `mpv`.

## Run Commands
Run the main app with `python3 iptv.py`.
Run the scheduled recorder directly with
`python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /tmp/test.ts`.
Run the utility script with `python3 util.py`.
Run the terminal diagnostic script with `python3 test_tty.py`.

## Docker Commands
Validate compose config with `docker-compose config -q`.
Build and start everything with `docker-compose up -d --build`.
Build/start only NGINX RTMP with `docker-compose up -d --build nginx-rtmp`.
View logs with `docker-compose logs -f nginx-rtmp`.
Stop services with `docker-compose down`.

## Lint And Validation
There is no checked-in config for `ruff`, `flake8`, `black`, `isort`,
`mypy`, `pylint`, `tox`, or `nox`.
There is also no `Makefile`, `pyproject.toml`, `setup.cfg`, or `pytest.ini`.
Safest repo-wide validation is syntax compilation:
`python3 -m py_compile iptv.py record_scheduled.py util.py test_tty.py`.
Single-file validation: `python3 -m py_compile iptv.py`.
Broader compile pass: `python3 -m compileall iptv.py record_scheduled.py util.py test_tty.py`.
If you modify Docker config, also run `docker-compose config -q`.

## Test Commands
Important: the current repo does not contain a formal automated test suite.
`test_tty.py` is the only checked-in test-like file and it is a smoke script.
Run it with `python3 test_tty.py`.
For a single-target validation after editing one file, use syntax compilation,
for example `python3 -m py_compile record_scheduled.py`.
If you add real tests later, prefer `pytest` and use explicit single-test runs like
`pytest path/to/test_file.py -k test_name`.
Do not assume pytest is already installed or configured here.

## Code Style
Follow existing code first and prefer the smallest correct change.
Do not refactor `iptv.py` broadly unless the task clearly requires it.
Preserve current CLI/menu behavior when editing menu handlers.
Use 4-space indentation.
Keep formatting readable; the repo is not under a strict autoformatter today.
Use docstrings where they help readers.
Do not add comments that only restate obvious code.

## Imports
Order imports as: standard library, third-party, local modules.
Avoid unused imports.
Use function-local imports only when the dependency is optional,
platform-specific, or only needed in one code path.
That pattern already exists in `iptv.py`.

## Types
The codebase is mostly dynamically typed.
`record_scheduled.py` uses some type hints; `iptv.py` mostly does not.
Add type hints to new helpers or new scripts when easy and local.
Do not do a large typing retrofit unless requested.
Prefer simple built-in types over heavy typing machinery.

## Naming
Use `snake_case` for functions, methods, variables, and filenames.
Use `PascalCase` for classes.
Use `UPPER_CASE` for module-level constants.
Keep names descriptive and action-oriented.
Examples already in the repo include `show_live_results`,
`build_nginx_container`, and `update_recording_status`.

## Error Handling
Prefer targeted exceptions over bare `except:` in new code.
The existing app sometimes catches broad exceptions for CLI resilience;
keep that only where failure must not break interactive flow.
For recoverable failures, log or print a clear message and return `False` or `None`.
This is the dominant pattern in `util.py`, `record_scheduled.py`,
and many helpers inside `iptv.py`.
Reserve `sys.exit(...)` for script-entry failures such as missing required config.
Preserve graceful degradation when optional tools are unavailable.

## Logging And Output
In `iptv.py`, prefer `console.print(...)` and existing `rich` output patterns.
In standalone scripts, prefer the `logging` module.
Keep messages short, actionable, and user-facing.
When shelling out to `ffmpeg` or Docker, report the failure cause when available.

## Data, SQL, And Subprocesses
Treat `.env`, `data/`, playlists, JSON files, and recordings as user state.
Do not delete or overwrite them casually.
Prefer storing new persistent app data under `data/`.
Be careful with artifacts mirrored into both `data/` and `nginx/html/`.
Use parameterized SQL queries, not string interpolation.
When calling subprocesses, pass argument lists explicitly.
Avoid `shell=True` unless there is a real need.

## Change Workflow
Read the surrounding function before editing it.
Many methods in `iptv.py` are stateful and connected through menus.
Do not clean up unrelated code while solving a focused issue.
If you add a new env var, file path, or user-facing workflow,
update `.env.example`, `README.md`, or both when relevant.
Before finishing, run the smallest validation that matches the change.
At minimum, run `python3 -m py_compile` on each edited Python file.

## Practical Notes
Prefer local edits over sweeping reorganizations.
Assume `data/` may contain real user state and recordings.
Be careful with menu return paths and interactive prompts in `iptv.py`.
When changing Docker behavior, keep the Python CLI and compose config aligned.
When changing recording behavior, verify both `iptv.py` and `record_scheduled.py` paths.
