#!/bin/bash
# Wrapper script for scheduled recordings
# This ensures the correct Python environment is used

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
source venv/bin/activate
exec python3 "$SCRIPT_DIR/record_scheduled.py" "$@"
