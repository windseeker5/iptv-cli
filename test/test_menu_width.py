#!/usr/bin/env python3
"""Test terminal menu width capabilities"""

import os
from simple_term_menu import TerminalMenu

# Get terminal size
try:
    cols, rows = os.get_terminal_size()
    print(f"Terminal size: {cols} columns x {rows} rows")
except:
    print("Could not determine terminal size")
    cols = 80

# Test with different length options
test_options = [
    "Short",
    "Medium length option here",
    "This is a very long option that should definitely be longer than most terminal widths - let's see if it gets truncated by the menu system",
    "⭐10.0 2023 This is a movie title that goes on and on and on and on and on",
    "Back"
]

print("\nTest options (raw):")
for i, opt in enumerate(test_options):
    print(f"{i}: [{len(opt)} chars] {opt}")

print("\n" + "="*60)
print("Now showing in TerminalMenu (use arrow keys, ESC to exit):")
print("="*60)

try:
    menu = TerminalMenu(
        test_options,
        title="Test Menu Width",
        menu_cursor="> "
    )
    choice = menu.show()
    if choice is not None:
        print(f"\nYou selected option {choice}: {test_options[choice]}")
except KeyboardInterrupt:
    print("\nCancelled")