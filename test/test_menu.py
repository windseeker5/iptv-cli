#!/usr/bin/env python3
"""
Test script to reproduce the TerminalMenu issue
"""

from simple_term_menu import TerminalMenu
import os
import sys

def test_simple_menu():
    """Test a simple menu without any interference"""
    print("Testing simple TerminalMenu...")
    
    options = [
        "Option 1",
        "Option 2", 
        "Option 3",
        "Exit"
    ]
    
    terminal_menu = TerminalMenu(
        options,
        title="Test Menu:",
        menu_cursor="> "
    )
    
    choice = terminal_menu.show()
    print(f"You selected: {options[choice] if choice is not None else 'None'}")

def test_menu_with_clear():
    """Test menu with screen clearing"""
    os.system('clear')  # Use system clear instead of ANSI codes
    print("Content Details:")
    print("─" * 50)
    print("Test content information")
    print("─" * 50)
    print()
    
    options = [
        "🎬 Play with MPV",
        "📡 Restream", 
        "📋 Copy URL",
        "🔙 Back"
    ]
    
    terminal_menu = TerminalMenu(
        options,
        title="Select action:",
        menu_cursor="> "
    )
    
    choice = terminal_menu.show()
    print(f"Selected: {options[choice] if choice is not None else 'None'}")

if __name__ == "__main__":
    print("Menu Test 1: Simple menu")
    test_simple_menu()
    
    input("\nPress Enter for test 2...")
    
    print("\nMenu Test 2: Menu with screen clearing")
    test_menu_with_clear()