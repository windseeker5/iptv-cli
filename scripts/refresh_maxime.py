#!/usr/bin/env python3
"""Timer job: refresh Maxime's UMass schedule (times, TV, results) from Hockey East.

Run by the kdcli-refresh-maxime systemd user timer; JP's maxime_games tool reads the result.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iptv_tui.domain import maxime  # noqa: E402

try:
    result = maxime.refresh()
except Exception as exc:  # network down, site moved: keep the last good copy
    result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
print(json.dumps(result, ensure_ascii=False))
sys.exit(0 if result["ok"] else 1)
