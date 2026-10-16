"""The recording diary: one small JSON report per recording run.

record_scheduled.py and recording_pipeline.py add an event for every step
(start, each attempt and dropout, join, length check, placement, Jellyfin,
Discord). The report is saved after every event, so a crash still leaves the
story up to that point. JP reads these instead of ffmpeg progress logs.

Never put secrets in a report: no provider URL, no credentials, no webhook.
"""

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "runs"

OK, WARNING, FAILED, RUNNING = "ok", "warning", "failed", "running"
_RANK = {OK: 0, WARNING: 1, FAILED: 2}
MAX_EMPTY_ATTEMPTS = 20

# Anything that looks like a credential-bearing URL is cut before it is stored.
_URL_RE = re.compile(r"https?://\S+")


def _clean(text: str) -> str:
    return _URL_RE.sub("<url>", str(text))[:400]


def format_seconds(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m{sec:02d}s" if h else f"{m}m{sec:02d}s"


class RunReport:
    def __init__(self, run_id: str, **meta):
        self.path = RUNS_DIR / f"{run_id}.json"
        self.data = {
            "run_id": run_id,
            "status": RUNNING,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            **meta,
            "attempts": [],
            "events": [],
            "result": {},
        }
        self._status = OK
        self.save()

    # -- recording ---------------------------------------------------------
    def event(self, step: str, status: str, message: str, **data) -> None:
        """Add one step to the diary. status is ok / warning / failed."""
        self._status = max(self._status, status, key=lambda s: _RANK.get(s, 0))
        self.data["events"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "step": step,
            "status": status,
            "message": _clean(message),
            **{k: v for k, v in data.items() if v is not None},
        })
        self.save()

    def attempt(self, number: int, file: str, planned_seconds: int, captured_seconds: float,
                size_mb: float, ok: bool, wrote: bool, error: str | None = None) -> None:
        # A busy provider can mean hundreds of empty retries; the first are enough.
        # Attempts that captured footage are always kept.
        empty = sum(1 for a in self.data["attempts"] if not a["wrote_data"])
        if not wrote and empty >= MAX_EMPTY_ATTEMPTS:
            return
        self.data["attempts"].append({
            "attempt": number,
            "file": Path(file).name,
            "planned_seconds": planned_seconds,
            "captured_seconds": round(captured_seconds),
            "size_mb": round(size_mb, 1),
            "ok": ok,
            "wrote_data": wrote,
            "error": _clean(error) if error else None,
        })
        self.save()

    def set_result(self, **data) -> None:
        self.data["result"].update(data)
        self.save()

    @property
    def status(self) -> str:
        return self._status

    # -- finish ------------------------------------------------------------
    def finish(self, status: str | None = None) -> None:
        self.data["status"] = status or self._status
        self.data["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.save()

    def save(self) -> None:
        try:
            RUNS_DIR.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=RUNS_DIR, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, self.path)
        except OSError:
            pass  # the diary must never break a recording

    # -- summary -----------------------------------------------------------
    def problems(self) -> list[str]:
        return [e["message"] for e in self.data["events"] if e["status"] in (WARNING, FAILED)]


def load(run_id: str) -> dict | None:
    try:
        return json.loads((RUNS_DIR / f"{run_id}.json").read_text())
    except (OSError, ValueError):
        return None


def recent(limit: int = 10) -> list[dict]:
    """Newest reports first, trimmed to what a list needs."""
    try:
        files = sorted(RUNS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    except OSError:
        return []
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        out.append({k: d.get(k) for k in
                    ("run_id", "status", "channel_name", "started_at", "finished_at",
                     "planned_seconds", "show_name")} | {"result": d.get("result", {}),
                                                         "problems": [e["message"] for e in d.get("events", [])
                                                                      if e.get("status") in (WARNING, FAILED)]})
    return out
