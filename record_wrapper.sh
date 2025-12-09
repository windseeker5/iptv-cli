#!/bin/bash
# Wrapper script for scheduled recordings
# This ensures the correct Python environment is used

cd /home/kdresdell/Documents/DEV/iptv
source venv/bin/activate
exec python3 record_scheduled.py "$@"
