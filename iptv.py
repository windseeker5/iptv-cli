#!/usr/bin/env python3
"""Launcher for the new modular IPTV TUI."""

import os
import sys

# Auto-activate virtual environment and set working directory
script_dir = os.path.dirname(os.path.abspath(__file__))
venv_activate = os.path.join(script_dir, "venv", "bin", "activate_this.py")

# Change to script directory
os.chdir(script_dir)

# Activate virtual environment if it exists
if os.path.exists(venv_activate):
    with open(venv_activate) as f:
        exec(f.read(), {"__file__": venv_activate})
elif os.path.exists(os.path.join(script_dir, "venv", "bin", "python")):
    venv_python = os.path.join(script_dir, "venv", "bin", "python")
    if sys.executable != venv_python:
        os.execv(venv_python, [venv_python] + sys.argv)

from iptv_tui.app import IPTVApp


if __name__ == "__main__":
    IPTVApp().run()
