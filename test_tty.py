#!/usr/bin/env python3
"""Test script to diagnose TTY access issues"""

import sys
import os

print(f"Python version: {sys.version}")
print(f"Running from: {os.getcwd()}")
print(f"stdin isatty: {sys.stdin.isatty()}")
print(f"stdout isatty: {sys.stdout.isatty()}")
print(f"stderr isatty: {sys.stderr.isatty()}")

# Try to access /dev/tty
try:
    with open("/dev/tty", "r") as tty:
        print("/dev/tty: Successfully opened")
except Exception as e:
    print(f"/dev/tty: FAILED - {e}")

# Try to import and use simple-term-menu
try:
    from simple_term_menu import TerminalMenu
    print("simple-term-menu: Successfully imported")

    # Try to create a simple menu
    options = ["Option 1", "Option 2", "Exit"]
    terminal_menu = TerminalMenu(options, title="Test Menu")
    print("TerminalMenu: Successfully created")
except Exception as e:
    print(f"TerminalMenu: FAILED - {e}")
    import traceback
    traceback.print_exc()
