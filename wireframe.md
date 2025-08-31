# IPTV CLI Application Wireframe

Based on your screenshot, here's what I think you want:

## Simple Terminal Interface Structure

```
┌─────────────────────────────────────────────┐
│ ★ Welcome to IPTV Manager!                  │
│                                             │
│ Database: iptv.db | Status: Ready          │
│ cwd: /home/kdresdell/Documents/DEV/iptv     │
└─────────────────────────────────────────────┘

Main Menu:
1. Download IPTV Data
2. Browse Live Channels
3. Browse VOD Content
4. Search Content
5. Account Info
6. Exit

> Enter choice (1-6): _

? Type 'help' for more options
```

## Implementation Approach (Python Script)

### Option 1: Simple input/print based
```python
def show_welcome():
    print("┌─────────────────────────────────────────────┐")
    print("│ ★ Welcome to IPTV Manager!                  │")
    print("│ Database: iptv.db | Status: Ready          │")
    print("└─────────────────────────────────────────────┘")

def main_menu():
    while True:
        print("\nMain Menu:")
        print("1. Download IPTV Data")
        print("2. Browse Live Channels") 
        print("3. Browse VOD Content")
        print("4. Search Content")
        print("5. Account Info")
        print("6. Exit")
        
        choice = input("\n> Enter choice (1-6): ")
        handle_choice(choice)
```

### Option 2: Simple menu with basic arrow navigation
```python
from simple_term_menu import TerminalMenu

def main_menu():
    options = [
        "Download IPTV Data",
        "Browse Live Channels",
        "Browse VOD Content", 
        "Search Content",
        "Account Info",
        "Exit"
    ]
    
    menu = TerminalMenu(options, title="IPTV Manager")
    choice = menu.show()
```

## Key Features:
- Clean box drawing characters for headers
- Simple numbered menus OR arrow key navigation
- Basic status information display
- No complex TUI frameworks (no textual/rich panels)
- Just print() statements and input() prompts
- Uses existing simple-term-menu for navigation

Would you like me to implement this simple approach?