#!/bin/bash
# Sunday NOOVO V-TELE-HD recording (long block).
#
# Triggered by the systemd/iptv-noovo-record-sunday.timer user timer. Computes a
# fresh date-stamped output path each night, then runs the same
# record_scheduled.py path the TUI uses, with post-processing (remux ->
# Jellyfin library placement -> library scan) enabled.

set -euo pipefail

STREAM_ID=412858
CHANNEL_NAME="NOOVO V-TELE HD"
DURATION_SECONDS=9120
SHOW_NAME="Occupation Double"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
source venv/bin/activate

RECORDS_PATH="$(python3 -c '
from iptv_tui.domain import recordings
path, warning = recordings.resolve_records_path()
if warning:
    import sys
    print(warning, file=sys.stderr)
if not path:
    raise SystemExit(1)
print(path)
')"

OUTPUT_PATH="$(python3 -c "
from datetime import datetime
from iptv_tui.domain import recordings
print(recordings.generate_output_path('$RECORDS_PATH', '$CHANNEL_NAME', datetime.now(), start_now=True))
")"

LIBRARY_DIR="$(python3 -c '
from iptv_tui.domain import config
print(config.Config.NOOVO_LIBRARY_DIR)
')"

exec python3 "$SCRIPT_DIR/record_scheduled.py" \
    --stream-id "$STREAM_ID" \
    --duration "$DURATION_SECONDS" \
    --output "$OUTPUT_PATH" \
    --channel-name "$CHANNEL_NAME" \
    --postprocess-library-dir "$LIBRARY_DIR" \
    --postprocess-show-name "$SHOW_NAME"
