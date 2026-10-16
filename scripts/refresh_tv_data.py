#!/usr/bin/env python3
"""Timer job: refresh the channel list, and the full guide with --guide.

Run by the kdcli-refresh-channels / kdcli-refresh-guide systemd user timers.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iptv_tui.domain import tv_refresh  # noqa: E402

result = tv_refresh.refresh(guide="--guide" in sys.argv)
print(json.dumps(result))
sys.exit(0 if result["ok"] else 1)
