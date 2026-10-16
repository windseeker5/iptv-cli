"""Refresh the channel list (and, if asked, the full TV guide) from the provider.

One entry point for the timers (scripts/refresh_tv_data.py) and for JP's
refresh_channels tool, with a file lock so two refreshes never write the
database at the same time. Event channels (FLO hockey games...) are renamed
by the provider every day and appear a few hours before the game, so an old
list gives wrong answers.
"""
from __future__ import annotations

import fcntl
import time

from . import db, iptv_provider

LIVE = ["live_categories", "live_streams"]


def age_minutes(kind: str = "channels") -> int | None:
    ts = iptv_provider.last_refresh().get(kind)
    return None if not ts else int((time.time() - ts) // 60)


def refresh(guide: bool = False, if_older_than_min: int = 0) -> dict:
    """Download the live channel list (and the full guide if `guide`).

    Skips the channel download when the list is younger than `if_older_than_min`
    (handy when a timer and JP ask at the same moment)."""
    lock_path = db.data_dir() / "cache" / "refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # waits if another refresh is running
        out: dict = {"ok": True}
        age = age_minutes("channels")
        if if_older_than_min and age is not None and age < if_older_than_min:
            out["channels"] = f"already fresh ({age} min old)"
        else:
            r = iptv_provider.download_database(components=LIVE)
            out["ok"] = r["success"]
            out["channels"] = r["message"]
        if guide and out["ok"]:
            g = iptv_provider.download_full_epg(force=True)
            out["ok"] = g["success"]
            out["guide"] = g["message"]
    out["seconds"] = round(time.time() - started, 1)
    out["channels_age_min"] = age_minutes("channels")
    out["guide_age_min"] = age_minutes("guide")
    return out
