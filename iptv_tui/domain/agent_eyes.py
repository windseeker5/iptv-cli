"""JP's eyes: read-only checks for the owner.

Everything here only LOOKS: it reads run reports, probes files and the
provider, reads logs and code. Nothing changes a recording, a schedule, the
library or a file. Changes go through agent_actions.propose_* and the
Confirm tap.

Rules every function follows:
- Secrets never leave this module: provider URLs, logins, API keys and the
  Discord webhook are redacted from anything returned (see redact()).
- Files and code are read only from an allowlist (see _readable_source and
  _media_path); .env files, databases and data/ folders are never readable.
- Short: the runner kills a chat turn at 180 s, so probes sample seconds,
  never whole files. This module must never print to stdout (the MCP server
  speaks JSON over stdout).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import config, run_report, schedules

CLI_ROOT = Path(__file__).resolve().parents[2]            # kdtv/cli
DEV_ROOT = CLI_ROOT.parents[1]                            # ~/Documents/DEV
SOURCE_ROOTS = [CLI_ROOT, DEV_ROOT / "OMT"]
SKIP_DIRS = {"venv", ".git", "data", "downloads", "__pycache__", "node_modules", "hls", "nginx",
             "build", ".gradle", "static", "media", "recordings", "hero"}
SOURCE_SUFFIXES = {".py", ".sh", ".md", ".txt", ".html", ".js", ".css", ".service", ".timer", ".yml",
                   ".yaml", ".toml", ".conf"}
MEDIA_SUFFIXES = {".ts", ".mp4", ".mkv"}
RUNNING_STALE_HOURS = 6   # a "running" report older than this is a crashed run, not a live one


# ── secrets ──────────────────────────────────────────────────────────────
_SECRET_KEY = re.compile(r"(PASSWORD|API_KEY|TOKEN|SECRET|WEBHOOK|IPTV_USERNAME|IPTV_SERVER_URL)", re.I)
_secret_cache: list[str] | None = None


def _secret_values() -> list[str]:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    values: set[str] = set()
    for env_file in (CLI_ROOT / ".env", DEV_ROOT / ".env"):
        try:
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, _, value = line.partition("=")
                    value = value.strip().strip("'\"")
                    if _SECRET_KEY.search(key) and len(value) >= 5:
                        values.add(value)
        except OSError:
            pass
    for key, value in os.environ.items():
        if _SECRET_KEY.search(key) and len(value) >= 5:
            values.add(value)
    _secret_cache = sorted(values, key=len, reverse=True)
    return _secret_cache


_URL_CRED = re.compile(r"/live/[^/\s]+/[^/\s]+/")
_QUERY_CRED = re.compile(r"(username|password|token|api_key)=[^&\s]+", re.I)
_WEBHOOK = re.compile(r"https?://(?:discord(?:app)?\.com)/api/webhooks/\S+")


def redact(text: str) -> str:
    """Strip anything that could be a credential from text JP will see."""
    text = str(text)
    for value in _secret_values():
        text = text.replace(value, "***")
    text = _URL_CRED.sub("/live/***/***/", text)
    text = _QUERY_CRED.sub(r"\1=***", text)
    return _WEBHOOK.sub("<discord-webhook>", text)


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


# ── recordings: the diary ────────────────────────────────────────────────
def recent_runs(limit: int = 8) -> dict:
    """The latest recordings with their outcome. Start here for 'how did it go?'."""
    runs = run_report.recent(max(1, min(int(limit), 20)))
    for r in runs:
        r["planned"] = run_report.format_seconds(r.get("planned_seconds"))
        res = r.get("result") or {}
        if res.get("final_seconds"):
            r["recorded"] = run_report.format_seconds(res["final_seconds"])
        elif res.get("captured_seconds"):
            r["recorded"] = run_report.format_seconds(res["captured_seconds"])
        if r.get("status") == run_report.RUNNING and _is_stale(r.get("started_at")):
            r["status"] = "crashed"
            r["problems"] = (r.get("problems") or []) + ["the run never finished (stopped without a report)"]
    return {"runs": runs, "note": "status: ok / warning / failed / running / crashed. Use run_report for the steps."}


def _is_stale(started_at: str | None) -> bool:
    try:
        return (datetime.now() - datetime.fromisoformat(started_at)).total_seconds() > RUNNING_STALE_HOURS * 3600
    except (TypeError, ValueError):
        return False


def run_report_for(run_id: str) -> dict:
    """The step-by-step diary of one recording."""
    if not re.fullmatch(r"[\w.\-]+", run_id or ""):
        return {"error": "bad run id"}
    d = run_report.load(run_id)
    if not d:
        return {"error": f"no report named {run_id}", "hint": "use recent_runs to list them"}
    pieces = _pieces_on_disk(d.get("output"))
    return {
        "run_id": d["run_id"], "status": d["status"], "channel": d.get("channel_name"),
        "show_name": d.get("show_name"), "started_at": d["started_at"], "finished_at": d.get("finished_at"),
        "planned": run_report.format_seconds(d.get("planned_seconds")),
        "events": [f"{e['time']} [{e['status']}] {e['step']}: {redact(e['message'])}" for e in d["events"]],
        "attempts": d["attempts"], "result": d.get("result"),
        "pieces_still_on_disk": pieces,
    }


def _pieces_on_disk(output: str | None) -> list[dict]:
    if not output:
        return []
    try:
        from . import recording_pipeline
        segs = recording_pipeline.find_segments(output)
        return [{"name": s.name, "size_mb": round(s.stat().st_size / 1048576)} for s in segs]
    except Exception:
        return []


# ── files: does it look good? ────────────────────────────────────────────
def _media_roots() -> list[Path]:
    roots = [Path(recordings_fallback()), Path(schedules.tv_library_root()), Path(config.Config.USB_RECORDS_PATH)]
    return [r.resolve() for r in roots if str(r)]


def recordings_fallback() -> str:
    from . import recordings
    return str(recordings.FALLBACK_RECORDS_PATH)


def _media_path(target: str) -> tuple[Path | None, dict | None]:
    """Resolve a run id or a path to a media file inside the allowed folders."""
    report = None
    if re.fullmatch(r"[\w.\-]+", target) and run_report.load(target):
        report = run_report.load(target)
        res = report.get("result") or {}
        for candidate in (res.get("episode_path"), report.get("output")):
            if candidate and Path(candidate).exists():
                target = candidate
                break
        else:
            pieces = _pieces_on_disk(report.get("output"))
            if pieces:
                target = str(Path(report["output"]).parent / pieces[0]["name"])
            else:
                return None, report
    p = Path(target).expanduser()
    if not p.is_absolute():
        p = Path(recordings_fallback()) / p
    try:
        p = p.resolve()
    except OSError:
        return None, report
    if p.suffix.lower() not in MEDIA_SUFFIXES or not p.exists():
        return None, report
    if not any(p == root or root in p.parents for root in _media_roots()):
        return None, report
    return p, report


def check_file(target: str, samples: int = 5) -> dict:
    """Length, streams and a picture/sound check on a recording or library episode.

    target: a run id (from recent_runs) or a file path inside the recordings or
    TV library folders. Samples a few short stretches for black picture, frozen
    picture, silence and decode errors; it never decodes the whole file.
    """
    path, report = _media_path(target)
    if not path:
        return {"error": "file not found or not in an allowed folder",
                "hint": "pass a run id from recent_runs, or a path inside the recordings / TV library folders"}
    out: dict = {"file": path.name, "folder": str(path.parent), "size_mb": round(path.stat().st_size / 1048576),
                 "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes")}
    probe = _run(["ffprobe", "-v", "error", "-show_entries",
                  "format=duration:stream=codec_type,codec_name,width,height,r_frame_rate,channels",
                  "-of", "json", str(path)], timeout=60)
    try:
        import json
        info = json.loads(probe.stdout or "{}")
        duration = float(info["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return {**out, "error": "the file cannot be read as video (corrupt or incomplete)",
                "detail": redact((probe.stderr or "")[-300:])}
    out["duration"] = run_report.format_seconds(duration)
    out["duration_seconds"] = round(duration)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            out["video"] = f"{s.get('codec_name')} {s.get('width')}x{s.get('height')} @ {s.get('r_frame_rate')}"
        elif s.get("codec_type") == "audio":
            out["audio"] = f"{s.get('codec_name')} {s.get('channels')}ch"
    planned = (report or {}).get("planned_seconds")
    if planned:
        out["planned"] = run_report.format_seconds(planned)
        out["percent_of_planned"] = round(100 * duration / planned)

    # Sample short stretches spread over the file.
    n = max(2, min(int(samples), 8))
    findings, flagged = [], 0
    for i in range(n):
        at = duration * (0.05 + 0.9 * i / (n - 1))
        r = _run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "info", "-ss", f"{at:.1f}", "-t", "8", "-i", str(path),
                  "-vf", "blackdetect=d=2:pix_th=0.10,freezedetect=n=0.003:d=4",
                  "-af", "silencedetect=n=-50dB:d=4", "-f", "null", "-"], timeout=60)
        text = r.stderr or ""
        # Decoder complaints only (timestamp chatter from the muxer is harmless).
        bad = re.compile(r"error|invalid|exceeds|not allocated|reserved|corrupt|not allowed|failure|does not match",
                         re.I)
        video_errors = sum(1 for l in text.splitlines() if l.startswith("[h264") and bad.search(l))
        audio_errors = sum(1 for l in text.splitlines()
                           if re.match(r"\[(aac|ac3|eac3|mp2|mp3)", l) and bad.search(l))
        sample = {"at": run_report.format_seconds(at),
                  "black_picture": "black_start" in text,
                  "frozen_picture": "freeze_start" in text,
                  "silence": "silence_start" in text,
                  "picture_errors": video_errors, "sound_errors": audio_errors}
        if (sample["black_picture"] or sample["frozen_picture"] or sample["silence"]
                or video_errors > 5 or audio_errors > 5):
            flagged += 1
        findings.append(sample)
    out["samples"] = findings
    damaged_sound = [f["at"] for f in findings if f["sound_errors"] > 5]
    damaged_picture = [f["at"] for f in findings if f["picture_errors"] > 5]
    if damaged_sound:
        out["damaged_sound_at"] = damaged_sound
    if damaged_picture:
        out["damaged_picture_at"] = damaged_picture
    if "percent_of_planned" in out and out["percent_of_planned"] < 70:
        out["verdict"] = "BAD: much shorter than planned"
    elif "percent_of_planned" in out and out["percent_of_planned"] < 95:
        out["verdict"] = "CHECK: shorter than planned"
    elif damaged_sound or damaged_picture:
        what = " and ".join(x for x, v in (("sound", damaged_sound), ("picture", damaged_picture)) if v)
        out["verdict"] = (f"CHECK: damaged {what} in some samples (see damaged_*_at); the damage usually "
                          "runs from there to the next clean spot and is in the provider's feed, not fixable")
    elif flagged >= max(2, n // 2):
        out["verdict"] = "CHECK: many samples show black, frozen or silent stretches"
    elif flagged:
        out["verdict"] = "OK, with a few spots to look at (black/frozen can just be a title card or ad break)"
    else:
        out["verdict"] = "OK: full length, picture and sound present in every sample"
    return out


# ── provider and stream ──────────────────────────────────────────────────
def _provider_account() -> dict:
    """Provider account info (no secrets). Uses the app's browser-style
    session: this provider answers 403 to plain script requests."""
    from . import iptv_provider
    c = config.Config
    session = iptv_provider._epg_session()
    try:
        r = session.get(f"{c.IPTV_SERVER_URL}/player_api.php",
                        params={"username": c.IPTV_USERNAME, "password": c.IPTV_PASSWORD}, timeout=15)
    except Exception as exc:
        return {"reachable": False, "error": type(exc).__name__}
    if r.status_code != 200:
        return {"reachable": True, "http": r.status_code, "login_ok": False}
    try:
        info = r.json().get("user_info", {})
    except ValueError:
        return {"reachable": True, "http": 200, "login_ok": False, "error": "answer was not JSON"}
    exp = info.get("exp_date")
    days = round((int(exp) - time.time()) / 86400) if str(exp).isdigit() else None
    return {"reachable": True, "http": 200, "login_ok": bool(info.get("auth")),
            "account_status": info.get("status"), "expires_in_days": days,
            "connections_in_use": int(info.get("active_cons") or 0),
            "connections_allowed": int(info.get("max_connections") or 0)}


def probe_provider() -> dict:
    """Is the IPTV provider up, is the login accepted, is the one connection free?"""
    from . import tv_refresh
    out = _provider_account()
    try:
        out["channel_list_age_minutes"] = tv_refresh.age_minutes("channels")
    except Exception:
        pass
    if out.get("connections_allowed") == 1:
        out["note"] = ("The plan allows ONE connection. Watching TV, a restream, a stream test or a "
                       "recording each use it; a second one gets refused or cuts the first.")
    return out


def _connection_busy() -> str | None:
    """Why a stream test must not run now, or None when the provider's slot is free."""
    from . import restream
    for r in run_report.recent(5):
        if r.get("status") == run_report.RUNNING and not _is_stale(r.get("started_at")):
            return f"a recording is running now ({r.get('channel_name')})"
    try:
        if restream.get_active_restream():
            return "a restream is live and uses the provider's connection"
    except Exception:
        pass
    acct = _provider_account()
    if acct.get("connections_allowed") and acct.get("connections_in_use", 0) >= acct["connections_allowed"]:
        return "the provider says its connection is in use right now (someone is watching or recording)"
    return None


def probe_stream(stream_id: int, seconds: int = 8) -> dict:
    """Pull a few seconds of a live channel and report whether the feed is healthy.

    Uses the provider's connection for about ten seconds, so it refuses while a
    recording, restream or viewer is on.
    """
    from . import iptv_provider
    seconds = max(4, min(int(seconds), 15))
    busy = _connection_busy()
    if busy:
        return {"ok": False, "skipped": True, "reason": f"not tested: {busy}"}
    import tempfile
    url = iptv_provider.build_stream_url(int(stream_id))
    # Write to a temp file (deleted right after): the null muxer reports no
    # size or bitrate, and the file tells us what really arrived.
    with tempfile.TemporaryDirectory(prefix="jp-probe-") as tmp:
        out_file = Path(tmp) / "probe.ts"
        try:
            r = _run(["ffmpeg", "-nostdin", "-hide_banner", "-t", str(seconds), "-rw_timeout", "10000000",
                      "-i", url, "-map", "0", "-c", "copy", "-f", "mpegts", str(out_file)], timeout=seconds + 25)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "the provider did not answer in time (stalled stream)"}
        size = out_file.stat().st_size if out_file.exists() else 0
        got_seconds = _probe_seconds(out_file) if size else None
    text = redact((r.stderr or "").replace(url, "<stream>"))
    errors = [l.strip() for l in text.splitlines()
              if re.search(r"HTTP error|Server returned|Connection|timed out|Input/output error|Invalid data", l)][:4]
    video = re.search(r"Video: (\w+).*?(\d{3,4}x\d{3,4}).*?([\d.]+) fps", text)
    audio = re.search(r"Audio: (\w+)", text)
    ok = r.returncode == 0 and bool(got_seconds) and got_seconds >= seconds * 0.7
    return {"ok": ok, "asked_seconds": seconds,
            "received_seconds": round(got_seconds, 1) if got_seconds else None,
            "bitrate_kbps": round(size * 8 / 1000 / got_seconds) if got_seconds else None,
            "video": f"{video.group(1)} {video.group(2)} @ {video.group(3)} fps" if video else None,
            "audio": audio.group(1) if audio else None, "errors": errors}


def _probe_seconds(path: Path) -> float | None:
    try:
        out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                   timeout=20)
        return float(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


# ── whole-system health ──────────────────────────────────────────────────
def _jellyfin_status() -> dict:
    import requests
    c = config.Config
    base = (c.JELLYFIN_PUBLISHED_SERVER_URL or "").rstrip("/")
    if not base:
        return {"configured": False}
    out: dict = {"configured": True}
    try:
        pub = requests.get(f"{base}/System/Info/Public", timeout=10)
        out["reachable"] = pub.status_code == 200
        if pub.status_code == 200:
            out["version"] = pub.json().get("Version")
    except Exception as exc:
        return {**out, "reachable": False, "error": type(exc).__name__}
    if c.JELLYFIN_API_KEY:
        try:
            r = requests.get(f"{base}/System/Info", timeout=10,
                             headers={"Authorization": f'MediaBrowser Token="{c.JELLYFIN_API_KEY}"'})
            out["api_key_accepted"] = r.status_code == 200
        except Exception as exc:
            out["api_key_accepted"] = False
            out["error"] = type(exc).__name__
    else:
        out["api_key_accepted"] = None
        out["note"] = "no API key configured; scans use the Jellyfin login instead"
    return out


def _disk(path: str) -> dict:
    try:
        u = shutil.disk_usage(path)
        return {"path": path, "free_gb": round(u.free / 1e9, 1), "used_percent": round(100 * u.used / u.total)}
    except OSError as exc:
        return {"path": path, "error": type(exc).__name__}


def _backup_drive() -> dict:
    p = config.Config.USB_RECORDS_PATH
    exists = os.path.isdir(p)
    return {"path": p, "exists": exists, "writable": bool(exists and os.access(p, os.W_OK)),
            "effect": None if (exists and os.access(p, os.W_OK))
            else "recordings go to the local fallback folder instead"}


def _service_result(unit: str) -> dict:
    r = _run(["systemctl", "--user", "show", unit, "--property=Result,ExecMainStatus,InactiveEnterTimestamp",
              "--value"], timeout=10)
    lines = (r.stdout or "").splitlines()
    return {"result": lines[0] if lines else None, "last_finished": lines[2] if len(lines) > 2 else None}


def leftover_pieces(limit: int = 12) -> list[dict]:
    """Recording pieces sitting in the recordings folder that no live run owns:
    each one is a recording that did not make it to the library."""
    root = Path(recordings_fallback())
    live = {r["run_id"] for r in run_report.recent(10)
            if r.get("status") == run_report.RUNNING and not _is_stale(r.get("started_at"))}
    out = []
    try:
        files = [p for p in root.glob("*.ts") if p.is_file()]
    except OSError:
        return []
    for p in sorted(files, key=lambda f: f.stat().st_mtime, reverse=True):
        base = re.sub(r"\.part\d+$", "", p.stem)
        if base in live:
            continue
        out.append({"name": p.name, "size_mb": round(p.stat().st_size / 1048576),
                    "age_hours": round((time.time() - p.stat().st_mtime) / 3600),
                    "has_report": run_report.load(base) is not None})
        if len(out) >= limit:
            break
    return out


def system_health() -> dict:
    """One look at everything a recording depends on."""
    from . import agent_actions as A
    scheds = []
    for s in schedules.list_schedules():
        scheds.append({"id": s.id, "what": s.describe(), "enabled": s.enabled,
                       "next_run": schedules.next_run(s.id), **_service_result(f"{s.unit}.service")})
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "provider": _provider_account(),
        "jellyfin": _jellyfin_status(),
        "backup_drive": _backup_drive(),
        "disk_recordings": _disk(recordings_fallback()),
        "disk_library": _disk(schedules.tv_library_root()),
        "restream": A.restream_status(),
        "schedules": scheds,
        "discord_webhook_configured": bool(os.getenv("DISCORD_WEBHOOK_URL") or _env_has("DISCORD_WEBHOOK_URL")),
        "leftover_pieces": leftover_pieces(),
        "last_preflight": last_preflight(),
    }


def _env_has(key: str) -> bool:
    try:
        return any(l.startswith(f"{key}=") and len(l) > len(key) + 2 for l in (CLI_ROOT / ".env").read_text().splitlines())
    except OSError:
        return False


PREFLIGHT_LOG = CLI_ROOT / "data" / "preflight.jsonl"


def last_preflight() -> dict | None:
    try:
        import json
        lines = PREFLIGHT_LOG.read_text().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


# ── logs ─────────────────────────────────────────────────────────────────
_SINCE_OK = re.compile(r"^[0-9A-Za-z :.\-]{1,40}$")


def read_log(unit: str | None = None, since: str = "24 hours ago", grep: str | None = None, lines: int = 80) -> dict:
    """Journal of a KD CLI unit (recordings, refresh jobs, JP's runner).

    Progress noise is dropped, credentials are redacted, output is capped.
    Without `unit`, lists the units that can be read.
    """
    if not unit:
        r = _run(["systemctl", "--user", "list-units", "--all", "--no-legend", "--plain", "kdcli-*"], timeout=15)
        units = sorted({l.split()[0] for l in (r.stdout or "").splitlines() if l.strip()})
        return {"units": units, "hint": "pass one of these as `unit`; recordings are kdcli-rec-*.service"}
    if not re.fullmatch(r"kdcli-[\w.\-]+\.(service|timer)", unit):
        return {"error": "only kdcli-* units can be read"}
    if not _SINCE_OK.match(since or ""):
        return {"error": "bad `since`; use e.g. '2026-10-11 18:00' or '6 hours ago'"}
    lines = max(10, min(int(lines), 200))
    r = _run(["journalctl", "--user", "-u", unit, f"--since={since}", "--no-pager", "-o", "short-iso", "-n", "150000"],
             timeout=30)
    rows = [l for l in (r.stdout or "").splitlines()
            if "FFmpeg: frame=" not in l and "non-existing" not in l and "no frame!" not in l and l.strip()]
    if grep:
        g = grep.lower()
        rows = [l for l in rows if g in l.lower()]
    total = len(rows)
    return {"unit": unit, "since": since, "matching_lines": total,
            "lines": [redact(l)[:300] for l in rows[-lines:]],
            "note": None if total <= lines else f"showing the last {lines} of {total}; narrow with grep or since"}


# ── code ─────────────────────────────────────────────────────────────────
def _readable_source(p: Path) -> bool:
    if p.name.startswith(".env") or p.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    try:
        rp = p.resolve()
    except OSError:
        return False
    for root in SOURCE_ROOTS:
        root = root.resolve()
        if rp == root or root in rp.parents:
            rel = rp.relative_to(root).parts
            return not any(part in SKIP_DIRS for part in rel[:-1])
    return False


def _iter_source():
    for root in SOURCE_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                p = Path(dirpath) / name
                if _readable_source(p):
                    yield p


def search_source(query: str, limit: int = 25) -> dict:
    """Find where something is in the code (kdtv/cli and the OMT site)."""
    q = (query or "").strip().lower()
    if len(q) < 3:
        return {"error": "query must be at least 3 characters"}
    hits = []
    for p in _iter_source():
        try:
            for n, line in enumerate(p.read_text(errors="replace").splitlines(), start=1):
                if q in line.lower():
                    hits.append({"file": str(p), "line": n, "text": redact(line.strip())[:200]})
                    if len(hits) >= max(1, min(int(limit), 50)):
                        return {"matches": hits, "truncated": True}
        except OSError:
            continue
    return {"matches": hits, "truncated": False}


def read_source(path: str, start: int = 1, lines: int = 120) -> dict:
    """Read part of a code file (read-only, secrets redacted, never .env or data/)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = CLI_ROOT / p
    if not _readable_source(p) or not p.is_file():
        return {"error": "that file is not readable here (only code and docs in kdtv/cli and OMT)"}
    try:
        rows = p.read_text(errors="replace").splitlines()
    except OSError as exc:
        return {"error": type(exc).__name__}
    start = max(1, int(start))
    lines = max(10, min(int(lines), 200))
    chunk = rows[start - 1:start - 1 + lines]
    return {"file": str(p.resolve()), "start": start, "end": start - 1 + len(chunk), "total_lines": len(rows),
            "text": "\n".join(f"{start + i}: {redact(l)}" for i, l in enumerate(chunk))}
