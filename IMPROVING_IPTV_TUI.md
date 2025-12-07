# Improving IPTV TUI

## Part 1: Business Requirements

### What You Want

1. **Simple Search Flow**
   - Type a search term (e.g., "tva sport")
   - See matching results (live channels and VOD)
   - Navigate up/down through results with arrow keys

2. **EPG Information Display**
   - For each live channel result, see:
     - Channel name
     - What is currently playing (show/program title)
     - Full description of the current program
   - This information must be visible WITHOUT pressing extra keys or opening extra menus

3. **Single-Key Actions**
   - While navigating results, press one key to act:
     - `p` = Play the highlighted channel
     - `s` = Save to favorites
     - `d` = Delete from favorites
     - `r` = Restream
     - `c` = Download
   - NO extra confirmation menus
   - NO extra steps
   - Action executes immediately

4. **Visual Consistency**
   - Keep the "Welcome to IPTV" header visible throughout navigation
   - Beautiful, clean terminal interface
   - No truncated text - all EPG info must be fully visible

### What You Do NOT Want

- Numbers in front of results
- Extra menus after selecting an item
- Truncated EPG information
- Multiple steps to perform a simple action
- Complicated workflows

---

## Part 2: Technical Proposal

### Current Problem

The `simple-term-menu` library truncates menu item text to fit terminal width. This is a hardcoded behavior in the library (line 1113 in simple_term_menu.py):
```python
self._tty_out.write(menu_entry[: num_cols - all_cursors_width - shortcut_string_len])
```

This means any EPG info added to menu items gets cut off and becomes invisible.

### Proposed Solution: Split-Screen Layout with Preview Panel

**Tool: `simple-term-menu` with `preview_command` feature**

The same library has a built-in preview feature that displays a separate panel alongside the menu. This preview panel is NOT subject to the same truncation.

**Why this tool:**
- Already installed and used in the project
- No new dependencies needed
- `preview_command` callback updates dynamically as user navigates
- Preview panel can show unlimited text
- Single-key actions (`accept_keys`) still work

**Alternative considered: Pure Rich library**
- Would require custom keyboard handling
- More complex implementation
- Would need to rebuild arrow key navigation from scratch

### Recommendation

Use `simple-term-menu` with `preview_command` for the split-screen layout.

---

## Part 3: Wireframe Proposal

### Main Menu (Welcome Screen)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ✻ Welcome to                                                               │
│                                                                             │
│      ██╗██████╗ ████████╗██╗   ██╗                                          │
│      ██║██╔══██╗╚══██╔══╝██║   ██║                                          │
│      ██║██████╔╝   ██║   ██║   ██║                                          │
│      ██║██╔═══╝    ██║   ╚██╗ ██╔╝                                          │
│      ██║██║        ██║    ╚████╔╝                                           │
│      ╚═╝╚═╝        ╚═╝     ╚═══╝                                            │
│                                                                             │
│  ╭─────────────────────────────────────────────────────────────────────╮    │
│  │ Live: 12,345 | VOD: 5,678 | Account expires: 2025-06-15            │    │
│  ╰─────────────────────────────────────────────────────────────────────╯    │
│                                                                             │
│  > Search IPTV                                                              │
│    Discovery Hub                                                            │
│    YouTube Tool                                                             │
│    Update IPTV db                                                           │
│    Streaming Infrastructure                                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Search Input Screen

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ✻ Welcome to                                                               │
│                                                                             │
│      ██╗██████╗ ████████╗██╗   ██╗                                          │
│      ██║██╔══██╗╚══██╔══╝██║   ██║                                          │
│      ██║██████╔╝   ██║   ██║   ██║                                          │
│      ██║██╔═══╝    ██║   ╚██╗ ██╔╝                                          │
│      ██║██║        ██║    ╚████╔╝                                           │
│      ╚═╝╚═╝        ╚═╝     ╚═══╝                                            │
│                                                                             │
│  ╭─────────────────────────────────────────────────────────────────────╮    │
│  │ Enter your search term                                              │    │
│  ╰─────────────────────────────────────────────────────────────────────╯    │
│                                                                             │
│   > tva sport_                                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Search Results Screen (THE KEY SCREEN)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ✻ Welcome to IPTV                                                          │
│                                                                             │
│  ╭─ Search Results: 'tva sport' (4 found) ─────────────────────────────╮    │
│  │                                                                     │    │
│  │  (p)lay  (s)ave  (d)elete  (r)estream  (i)nfo  |  ESC = back       │    │
│  │                                                                     │    │
│  ╰─────────────────────────────────────────────────────────────────────╯    │
│                                                                             │
│  ╭─ Channel Info ──────────────────────────────────────────────────────╮    │
│  │                                                                     │    │
│  │  ⭐ CA FR: TVA SPORTS 2 HD                                          │    │
│  │                                                                     │    │
│  │  NOW PLAYING: Ultimate Tennis Showdown                              │    │
│  │                                                                     │    │
│  │  DESCRIPTION: Live coverage of the championship tennis match        │    │
│  │  featuring top players from around the world competing for the      │    │
│  │  grand prize. Commentary in French.                                 │    │
│  │                                                                     │    │
│  ╰─────────────────────────────────────────────────────────────────────╯    │
│                                                                             │
│  > ⭐ CA FR: TVA SPORTS 2 HD                                                │
│    ⭐ CA FR: TVA SPORTS HD                                                  │
│       CA: TVA SPORTS                                                        │
│    ⭐ CA: TVA SPORTS 2 HD                                                   │
│    ← Back                                                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**How it works:**
- Arrow UP/DOWN moves the `>` cursor between channels
- The "Channel Info" box updates automatically to show the highlighted channel's EPG data
- Press `p` = plays immediately
- Press `s` = saves to favorites immediately
- Press `ESC` = goes back
- NO extra menus, NO extra steps

### After Pressing Play

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  MPV player opens and plays the stream...                                   │
│                                                                             │
│  (User watches the stream)                                                  │
│                                                                             │
│  When MPV closes, user returns to Search Results screen                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part 4: Implementation Summary

### Changes Required

1. **File:** `/home/kdresdell/Documents/DEV/iptv/iptv.py`

2. **Method to modify:** `show_unified_results()`

3. **Key changes:**
   - Use `preview_command` parameter in TerminalMenu
   - Preview shows: Channel name, Now Playing, Description
   - Menu shows: Short channel names only
   - Single-key actions via `accept_keys`

### Code Structure

```python
def show_unified_results(self, live_results, vod_results, search_term):
    # 1. Fetch EPG data (title + description) for all live channels
    # 2. Build short menu options (just channel names)
    # 3. Create preview_info() function that returns EPG details
    # 4. Create TerminalMenu with preview_command=preview_info
    # 5. Handle single-key actions (p, s, d, r, c)
```

---

## Status

- [ ] Business requirements documented
- [ ] Technical proposal documented
- [ ] Wireframe designed
- [ ] Implementation pending approval
