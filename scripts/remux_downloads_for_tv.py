#!/usr/bin/env python3
"""
Remux downloaded videos to TV-compatible MP4, on demand.

Downloads are saved verbatim from the provider (often .mkv with an embedded
ASS/SSA subtitle track), which plays fine on a PC but a number of smart-TV
apps / Jellyfin device combos fail to direct-play. This walks data/downloads/
and remuxes every non-.mp4 video into .mp4 (stream copy only, no re-encode —
video/audio bits are untouched, only the container and subtitle format
change). Run it whenever you want, after downloading new episodes.

Usage:
    python3 scripts/remux_downloads_for_tv.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from iptv_tui.domain import db
from iptv_tui.domain.remux import remux_for_tv

VIDEO_EXTENSIONS = {".mkv", ".ts", ".avi", ".mov", ".webm", ".flv"}


def main():
    downloads_dir = db.data_dir() / "downloads"
    files = sorted(
        p for p in downloads_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )

    if not files:
        print(f"No non-MP4 video files found under {downloads_dir}")
        return

    print(f"Found {len(files)} file(s) to remux under {downloads_dir}\n")

    remuxed, failed = 0, 0
    for path in files:
        print(f"  {path.name} ... ", end="", flush=True)
        result = remux_for_tv(str(path))
        if result["success"]:
            print("OK")
            remuxed += 1
        else:
            print(f"FAILED — {result['message']}")
            failed += 1

    print(f"\n{remuxed} remuxed, {failed} failed.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
