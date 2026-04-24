#!/bin/bash
# Wrapper script for scheduled recordings
# This ensures the correct Python environment is used

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate
exec python3 record_scheduled.py "$@"
