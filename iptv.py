#!/usr/bin/env python3
"""
Menu-Driven IPTV CLI with Arrow Key Navigation
Similar to Chris Titus linutil interface style

Install: pip install -r requirements.txt
Run: python3 menu_driven_iptv.py
"""

import os
import sys

# Auto-activate virtual environment and set working directory
script_dir = os.path.dirname(os.path.abspath(__file__))
venv_activate = os.path.join(script_dir, 'venv', 'bin', 'activate_this.py')

# Change to script directory
os.chdir(script_dir)

# Activate virtual environment if it exists
if os.path.exists(venv_activate):
    with open(venv_activate) as f:
        exec(f.read(), {'__file__': venv_activate})
elif os.path.exists(os.path.join(script_dir, 'venv', 'bin', 'python')):
    # Alternative method if activate_this.py doesn't exist
    venv_python = os.path.join(script_dir, 'venv', 'bin', 'python')
    if sys.executable != venv_python:
        os.execv(venv_python, [venv_python] + sys.argv)
import sqlite3
import requests
import time
import textwrap
import json
import subprocess
import signal
import glob
import re
import base64
from datetime import datetime, timedelta
import threading
from dotenv import load_dotenv
from simple_term_menu import TerminalMenu
from rich.console import Console
from pyfiglet import Figlet
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
import yt_dlp

console = Console()

# Load environment variables
load_dotenv()

class IPTVMenuManager:
    def __init__(self):
        # Create data directory if it doesn't exist
        self.data_dir = "data"
        os.makedirs(self.data_dir, exist_ok=True)
        
        self.db_path = os.path.join(self.data_dir, "iptv.db")
        
        # Load credentials from environment variables
        self.server = os.getenv('IPTV_SERVER_URL')
        self.username = os.getenv('IPTV_USERNAME')
        self.password = os.getenv('IPTV_PASSWORD')
        self.inject_server = os.getenv('INJECT_SERVER_URL')
        
        # Validate required environment variables
        if not all([self.server, self.username, self.password]):
            missing_vars = []
            if not self.server: missing_vars.append('IPTV_SERVER_URL')
            if not self.username: missing_vars.append('IPTV_USERNAME')
            if not self.password: missing_vars.append('IPTV_PASSWORD')
            
            console.print(f"[red]Error: Missing required environment variables: {', '.join(missing_vars)}[/red]")
            console.print("Please check your .env file and ensure all IPTV credentials are set.")
            console.print("Copy .env.example to .env and add your credentials.")
            sys.exit(1)
        
        # Check database age and auto-update if needed
        self.auto_update_database_if_needed()

        # Ensure scheduled recordings table exists (persists across database updates)
        self.ensure_scheduled_recordings_table()

        # Detect platform capabilities for hardware acceleration
        self._is_raspberry_pi = None  # Cache detection result

    def ensure_scheduled_recordings_table(self):
        """Ensure scheduled_recordings table exists (survives database rebuilds)"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stream_id INTEGER,
                    channel_name TEXT,
                    start_time INTEGER,
                    duration INTEGER,
                    output_path TEXT,
                    timer_unit TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at INTEGER
                )
            ''')
            conn.commit()
            conn.close()
        except Exception as e:
            console.print(f"[yellow]Warning: Could not create scheduled_recordings table: {e}[/yellow]")

    def is_raspberry_pi(self):
        """Detect if running on Raspberry Pi for hardware acceleration"""
        if self._is_raspberry_pi is not None:
            return self._is_raspberry_pi

        try:
            # Check /proc/device-tree/model for Raspberry Pi
            if os.path.exists('/proc/device-tree/model'):
                with open('/proc/device-tree/model', 'r') as f:
                    model = f.read()
                    if 'Raspberry Pi' in model:
                        self._is_raspberry_pi = True
                        return True

            # Fallback: Check /proc/cpuinfo for BCM chip
            if os.path.exists('/proc/cpuinfo'):
                with open('/proc/cpuinfo', 'r') as f:
                    cpuinfo = f.read()
                    if 'BCM' in cpuinfo or 'Raspberry Pi' in cpuinfo:
                        self._is_raspberry_pi = True
                        return True
        except Exception:
            pass

        self._is_raspberry_pi = False
        return False

    def wait_for_escape(self):
        """Wait for escape key instead of enter"""
        import termios, sys, tty
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            while True:
                char = sys.stdin.read(1)
                if ord(char) == 27:  # ESC key
                    break
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except:
            # Fallback for environments where termios doesn't work
            input()

    def input_with_esc(self, prompt=" > "):
        """Like input() but returns None if ESC is pressed."""
        import termios, sys, tty
        sys.stdout.write(f"\n{prompt}")
        sys.stdout.flush()
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        buf = []
        try:
            tty.setcbreak(fd)
            while True:
                ch = sys.stdin.read(1)
                code = ord(ch)
                if code == 27:          # ESC
                    return None
                elif code in (10, 13):  # Enter
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    return "".join(buf)
                elif code == 127:       # Backspace
                    if buf:
                        buf.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                elif 32 <= code < 127 or code > 127:  # printable + UTF-8
                    buf.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
        except Exception:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            return input(prompt)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def check_database_age(self):
        """Check if database is older than 14 days"""
        if not os.path.exists(self.db_path):
            return True  # Database doesn't exist, needs creation
        
        try:
            # Get file modification time
            file_modified_time = os.path.getmtime(self.db_path)
            current_time = datetime.now().timestamp()
            
            # Calculate age in days
            age_in_seconds = current_time - file_modified_time
            age_in_days = age_in_seconds / (24 * 60 * 60)
            
            return age_in_days > 14
        except Exception as e:
            console.print(f"[yellow]Warning: Could not check database age: {e}[/yellow]")
            return False
    
    def get_database_age_days(self):
        """Get database age in days"""
        if not os.path.exists(self.db_path):
            return None
        
        try:
            file_modified_time = os.path.getmtime(self.db_path)
            current_time = datetime.now().timestamp()
            age_in_seconds = current_time - file_modified_time
            age_in_days = age_in_seconds / (24 * 60 * 60)
            return age_in_days
        except:
            return None
    
    def auto_update_database_if_needed(self):
        """Automatically update database if it's older than 14 days"""
        if not self.check_database_age():
            return  # Database is fresh, no update needed
        
        console.clear()
        console.print(Panel.fit("Database Auto-Update", style="bright_yellow"))
        
        age_days = self.get_database_age_days()
        if age_days is None:
            console.print("[yellow]⚠ Database not found. Creating new database...[/yellow]")
        else:
            console.print(f"[yellow]⚠ Database is {age_days:.1f} days old (>14 days).[/yellow]")
        
        console.print("[bright_cyan]Starting automatic database update...[/bright_cyan]")
        console.print("This may take a few minutes depending on your connection.")
        console.print()
        
        # Perform full database download
        success = self._download_and_create_db([
            "account_info", "live_categories", "live_streams", 
            "vod_categories", "vod_streams", "series_categories"
        ])
        
        if success:
            console.print()
            console.print("[green]✓ Database updated successfully![/green]")
            console.print("[dim white]Continuing in 2 seconds...[/dim white]")
            time.sleep(2)
        else:
            console.print()
            console.print("[red]✗ Database update failed![/red]")
            console.print("You can try updating manually from Settings.")
            console.print("[dim white]Continuing in 2 seconds...[/dim white]")
            time.sleep(2)
        
    def main_menu(self):
        """Main menu with arrow key navigation"""
        while True:
            console.clear()
            self.display_header(show_status=True)

            fav_count = self._get_favorites_count()
            fav_label = f"My Starred List  ({fav_count} saved)" if fav_count else "My Starred List  (empty)"
            options = [
                "Search Channels & Movies",
                "Browse by Category",
                fav_label,
                "Scheduled Recordings",
                "Settings"
            ]

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "x"),
                cycle_cursor=True,
                clear_screen=False
            )

            choice = terminal_menu.show()
            key = terminal_menu.chosen_accept_key

            # Handle X key - stop restream
            if key == 'x':
                active = self.get_active_restream()
                if active:
                    self.stop_restream_quick()
                continue

            if choice is None:
                console.print("\nGoodbye!")
                break
            elif choice == 0:
                self.unified_search_menu()
            elif choice == 1:
                self.browse_categories_menu()
            elif choice == 2:
                self.view_favorites_menu()
            elif choice == 3:
                self.view_scheduled_recordings()
            elif choice == 4:
                self.settings_menu()
    
    def _get_favorites_count(self):
        """Return number of starred items (safe for menu label use)"""
        try:
            return len(self.load_favorites())
        except:
            return 0

    def get_service_status_cached(self):
        """Return service status dict, refreshing at most every 30 seconds."""
        now = time.time()
        if not hasattr(self, '_svc_cache_time') or now - self._svc_cache_time > 30:
            self._svc_cache = {
                'nginx':    self.check_container_status(),
                'jellyfin': self.check_jellyfin_status(),
                'samba':    self.check_samba_status(),
            }
            self._svc_cache_time = now
        return self._svc_cache

    def _svc_dot(self, status_str):
        """Return colored dot based on service status string."""
        if "[green]" in status_str:
            return "[green]●[/green]"
        elif "[yellow]" in status_str:
            return "[yellow]●[/yellow]"
        else:
            return "[red]●[/red]"

    def show_status(self):
        """Show current database status"""
        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                live_count = cursor.execute("SELECT COUNT(*) FROM live_streams").fetchone()[0]
                vod_count = cursor.execute("SELECT COUNT(*) FROM vod_streams").fetchone()[0]
                
                # Get account info
                try:
                    account = cursor.execute("SELECT * FROM account_info LIMIT 1").fetchone()
                except:
                    account = None
                
                conn.close()
                
                # Build status lines
                status_lines = []
                
                # Get database age
                age_days = self.get_database_age_days()
                if age_days is not None:
                    if age_days < 1:
                        age_str = "[bright_cyan bold]Updated today[/bright_cyan bold]"
                    elif age_days < 2:
                        age_str = "[bright_cyan bold]1 day old[/bright_cyan bold]"
                    elif age_days <= 7:
                        age_str = f"[bright_cyan bold]{age_days:.0f} days old[/bright_cyan bold]"
                    else:
                        # Use yellow for old databases
                        age_str = f"[bright_yellow bold]{age_days:.0f} days old[/bright_yellow bold]"
                    
                    status_lines.append(f"[green]●[/green] [bright_cyan bold]Database: Ready[/bright_cyan bold] | {age_str}")
                else:
                    status_lines.append(f"[green]●[/green] [bright_cyan bold]Database: Ready[/bright_cyan bold]")
                
                # Add statistics
                status_lines.append(f"  Live Channels: {live_count:,}")
                status_lines.append(f"  VOD Content: {vod_count:,}")
                status_lines.append(f"  Total Content: {live_count + vod_count:,}")
                
                # Add account info if available
                if account:
                    exp_date = datetime.fromtimestamp(int(account[2])).strftime('%Y-%m-%d')
                    status_lines.append(f"  Account Status: {account[1]}")
                    status_lines.append(f"  Expires: {exp_date}")
                    status_lines.append(f"  Max Connections: {account[3]}")

                # Service health row
                svc = self.get_service_status_cached()
                ng   = self._svc_dot(svc['nginx'])
                jf   = self._svc_dot(svc['jellyfin'])
                sb   = self._svc_dot(svc['samba'])
                status_lines.append("")
                status_lines.append(f"  Services: {ng} nginx-rtmp  {jf} jellyfin  {sb} samba")

                # Check for active restream
                active_restream = self.get_active_restream()
                if active_restream:
                    status_lines.append("")
                    status_lines.append(f"[red]●[/red] [bright_red bold]Restreaming:[/bright_red bold] {active_restream['channel_name']}")
                    status_lines.append(f"  [dim]Press (X) to stop[/dim]")

                # Join all lines and display in panel
                status = "\n".join(status_lines)
                console.print(Panel(status, style="dim white"))
            except:
                console.print(Panel("[dim white]●[/dim white] Database: Error reading", style="dim white"))
        else:
            console.print(Panel("[dim white]●[/dim white] Database: Not found - Use 'Download/Update Database'", style="dim white"))

    def display_header(self, show_status=True):
        """Display the welcome header with optional status panel"""
        console.print()
        console.print("[bright_red] ✻[/bright_red] Welcome to")

        figlet = Figlet(font='isometric1')
        title = figlet.renderText('IPTV')
        console.print(f"[cyan]{title}[/cyan]")

        if show_status:
            self.show_status()

    def download_menu(self):
        """Download/Update menu"""
        while True:
            console.clear()
            console.print(Panel.fit("Download/Update Database", style="dim white"))
            
            options = [
                "Download Fresh Data (Full Update)",
                "Quick Update (Live Streams Only)",
                "Download VOD Only"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC
                break
            elif choice == 0:  # Full download
                self.download_full()
            elif choice == 1:  # Quick update
                self.download_live_only()
            elif choice == 2:  # VOD only
                self.download_vod_only()
    
    def unified_search_menu(self):
        """Unified search menu for live channels, VOD, and series content"""
        if not self.check_database():
            return

        while True:
            console.clear()
            self.display_header(show_status=False)
            console.print(Panel.fit("Search Channels & Movies", style="dim white"))

            search_term = self.input_with_esc()
            if search_term is None:        # ESC → back to main menu
                return
            search_term = search_term.strip()
            if not search_term:
                return                     # empty Enter → back to main menu

            live_results   = self.search_live_channels(search_term)
            vod_results    = self.search_vod_content(search_term)
            series_results = self.search_series_content(search_term)

            if not live_results and not vod_results and not series_results:
                console.print(f"\nNo content found for '{search_term}'")
                self.wait_for_escape()
                return

            self.show_unified_results(live_results, vod_results, series_results, search_term)
            return
    
    def show_unified_results(self, live_results, vod_results, series_results, search_term):
        """Show unified search results with live channels, VOD, and series content"""
        # Load EPG data - first check cache, then fetch missing from network
        epg_cache = {}
        if live_results:
            # First pass: check what's already cached (instant)
            uncached_channels = []
            for result in live_results:
                epg_data = self.get_now_playing_local(result['stream_id'], fetch_if_missing=False)
                if epg_data:
                    epg_cache[str(result['stream_id'])] = epg_data
                else:
                    uncached_channels.append(result)

            # Second pass: fetch uncached EPG from network (shows progress)
            if uncached_channels:
                console.clear()
                self.display_header(show_status=False)
                console.print(Panel.fit(f"Loading EPG for {len(uncached_channels)} channels (caching for next time)...", style="dim white"))
                for result in uncached_channels:
                    console.print(f"[dim]Fetching: {result['name']}[/dim]")
                    epg_data = self.get_now_playing_local(result['stream_id'], result.get('name'), fetch_if_missing=True)
                    epg_cache[str(result['stream_id'])] = epg_data

        # Build results list
        all_results = []
        for result in live_results:
            all_results.append(('live', result))
        for result in vod_results:
            all_results.append(('vod', result))
        for result in series_results:
            all_results.append(('series', result))

        while True:
            console.clear()
            self.display_header(show_status=False)

            # Build status panel matching database style (full width)
            age_days = self.get_database_age_days()
            if age_days is not None:
                if age_days < 1:
                    age_str = "[bright_cyan bold]Updated today[/bright_cyan bold]"
                elif age_days < 2:
                    age_str = "[bright_cyan bold]1 day old[/bright_cyan bold]"
                elif age_days <= 7:
                    age_str = f"[bright_cyan bold]{age_days:.0f} days old[/bright_cyan bold]"
                else:
                    age_str = f"[bright_yellow bold]{age_days:.0f} days old[/bright_yellow bold]"
                db_line = f"[green]●[/green] [bright_cyan bold]Database[/bright_cyan bold] | {age_str}"
            else:
                db_line = "[green]●[/green] [bright_cyan bold]Database[/bright_cyan bold]"

            panel_lines = [
                db_line,
                f"  Search Results: '{search_term}' ({len(all_results)} found)",
                "",
                f"  [dim]LIVE: (p)lay (r)restream to NGINX (c)record now (t)schedule rec  |  VOD: (p)lay (c)download  |  ALL: (s)★star (d)✕unstar (i)info  |  ESC=back[/dim]"
            ]
            console.print(Panel("\n".join(panel_lines), style="dim white"))
            console.print()

            favorites_set = self.get_favorites_set()
            options = []

            # Build menu options with index as preview argument (using | separator)
            # IMPORTANT: Replace | in channel names with - to avoid breaking preview separator
            for idx, (result_type, result) in enumerate(all_results):
                # Get the right ID field based on result type
                if result_type == 'series':
                    item_id = result.get('series_id')
                else:
                    item_id = result.get('stream_id')
                is_fav = (item_id, result_type) in favorites_set
                fav = "⭐ " if is_fav else "   "

                # Replace | with - in names to avoid breaking the preview separator
                display_name = result['name'].replace('|', '-')

                if result_type == 'live':
                    opt = f"{fav}[LIVE] {display_name}|{idx}"
                elif result_type == 'vod':
                    rating = f"{result['rating']:.1f}" if result.get('rating') else 'N/A'
                    year = result.get('year') or 'N/A'
                    opt = f"{fav}[VOD] {rating} {year} {display_name}|{idx}"
                else:  # series
                    rating = f"{result['rating']:.1f}" if result.get('rating') else 'N/A'
                    opt = f"{fav}[SERIES] {rating} {display_name}|{idx}"

                options.append(opt)

            # Add empty line for spacing at the end of results
            options.append(" |spacer")

            # Preview function to show EPG info in side panel
            def preview_info(preview_arg):
                if preview_arg == 'spacer':
                    return ""
                try:
                    # preview_arg is now just the index number (pipes removed from names)
                    idx = int(preview_arg)
                    result_type, result = all_results[idx]

                    lines = []
                    # Wrap text to fit preview panel (use terminal width minus margins)
                    try:
                        term_width = os.get_terminal_size().columns
                        wrap_width = max(40, term_width - 10)  # Leave some margin
                    except:
                        wrap_width = 100

                    if result_type == 'live':
                        # Get EPG with upcoming info
                        epg_full = self.get_epg_with_upcoming(result['stream_id'])
                        now_playing = epg_full.get('now')
                        upcoming = epg_full.get('next')

                        # Now Playing section
                        lines.append("━━━ Now Playing ━━━")
                        if now_playing and now_playing.get('title'):
                            lines.append(now_playing['title'])
                            lines.append("")
                            if now_playing.get('description'):
                                wrapped = textwrap.fill(now_playing['description'], width=wrap_width)
                                lines.append(wrapped)
                        else:
                            # No EPG data - show full channel name as fallback
                            lines.append("Channel:")
                            wrapped_name = textwrap.fill(result['name'], width=wrap_width)
                            lines.append(wrapped_name)

                        # Upcoming section
                        lines.append("")
                        if upcoming and upcoming.get('title'):
                            # Format the start time
                            from datetime import datetime
                            start_dt = datetime.fromtimestamp(upcoming['start_time'])
                            time_str = start_dt.strftime("%H:%M")
                            lines.append(f"━━━ Upcoming at {time_str} ━━━")
                            lines.append(upcoming['title'])
                            lines.append("")
                            if upcoming.get('description'):
                                wrapped = textwrap.fill(upcoming['description'], width=wrap_width)
                                lines.append(wrapped)
                        else:
                            lines.append("━━━ Upcoming ━━━")
                            lines.append("No upcoming program information")

                    elif result_type == 'vod':
                        # VOD info
                        lines.append(f"Rating: {result.get('rating', 'N/A')}")
                        lines.append(f"Year: {result.get('year', 'N/A')}")
                        if result.get('genre'):
                            lines.append(f"Genre: {result['genre']}")
                    else:
                        # Series info
                        lines.append(f"Rating: {result.get('rating', 'N/A')}")
                        if result.get('genre'):
                            lines.append(f"Genre: {result['genre']}")
                        if result.get('category_name'):
                            lines.append(f"Category: {result['category_name']}")
                        lines.append("")
                        if result.get('plot'):
                            wrapped = textwrap.fill(result['plot'], width=wrap_width)
                            lines.append(wrapped)

                    return "\n".join(lines)
                except Exception as e:
                    return f"Error: {str(e)}"

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "p", "i", "s", "d", "r", "c", "t"),
                cycle_cursor=True,
                clear_screen=False,
                preview_command=preview_info,
                preview_title="Channel Info",
                preview_size=0.4,
            )

            choice = terminal_menu.show()
            key = terminal_menu.chosen_accept_key

            # ESC pressed - go back
            if choice is None:
                break

            # Skip if spacer selected
            if choice == len(all_results):
                continue

            if 0 <= choice < len(all_results):
                result_type, selected = all_results[choice]

                # Handle keyboard shortcuts
                if key == 'p':  # Play directly
                    if result_type == 'series':
                        self.show_series_info(selected)
                    else:
                        self.play_with_mpv(selected)
                elif key == 'enter':  # Open action menu
                    if result_type == 'live':
                        self.live_stream_action_menu(selected)
                    elif result_type == 'vod':
                        self.vod_action_menu(selected)
                    else:
                        self.show_series_info(selected)
                elif key == 't':  # Schedule recording (Timer)
                    if result_type == 'live':
                        self.schedule_recording(selected)
                    else:
                        console.print("[yellow]Schedule recording only available for live channels[/yellow]")
                        self.wait_for_escape()
                elif key == 'i':
                    if result_type == 'live':
                        self.show_live_stream_info(selected)
                    elif result_type == 'vod':
                        self.show_vod_info(selected)
                    else:
                        self.show_series_info(selected)
                elif key == 's':
                    res = self.save_to_favorites(selected, result_type)
                    if res == -1:
                        console.print("[yellow]Already starred[/yellow]")
                    elif res > 0:
                        console.print(f"[green]★[/green] [bright_cyan]Added to your starred list![/bright_cyan] ({res} total)")
                        console.print(f"[dim]  Playlist URL: http://localhost:8080/iptv.m3u[/dim]")
                        console.print(f"[dim]  Import in VLC, Kodi, or your TV app to watch starred channels[/dim]")
                    self.wait_for_escape()
                elif key == 'd':
                    res = self.remove_from_favorites(selected, result_type)
                    if res > 0:
                        console.print(f"[green]✓[/green] Removed from starred list")
                    else:
                        console.print("[yellow]Not in starred list[/yellow]")
                    self.wait_for_escape()
                elif key == 'r':
                    if result_type != 'series':
                        self.quick_restream(selected)
                    else:
                        console.print("[yellow]Restream not available for series[/yellow]")
                        self.wait_for_escape()
                elif key == 'c':
                    if result_type == 'vod':
                        self.download_vod_to_data(selected)
                    elif result_type == 'live':
                        self.download_live_to_data(selected)
                    else:
                        console.print("[yellow]Download not available for series (select episodes from info)[/yellow]")
                        self.wait_for_escape()
    
    def search_live_menu(self):
        """Search live channels menu"""
        if not self.check_database():
            return
        
        console.clear()
        console.print(Panel.fit("Search Live Channels", style="dim white"))
        
        try:
            search_term = input("\nEnter search term: ").strip()
            if not search_term:
                return
            
            results = self.search_live_channels(search_term)
            if not results:
                console.print(f"\nNo channels found for '{search_term}'")
                self.wait_for_escape()
                return
            
            self.show_live_results(results, search_term)
            
        except KeyboardInterrupt:
            return
    
    def search_live_channels(self, query):
        """Search live channels in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Tokenize search query for multi-word partial matching
        tokens = query.split()
        if not tokens:
            conn.close()
            return []

        # Build WHERE clause with AND for each token
        conditions = ' AND '.join(['name LIKE ?' for _ in tokens])
        params = [f'%{token}%' for token in tokens]

        sql = f"""
            SELECT name, category_name, stream_id, stream_url, epg_channel_id
            FROM live_streams
            WHERE {conditions}
            ORDER BY name
            LIMIT 50
        """

        results = cursor.execute(sql, params).fetchall()
        conn.close()

        return [dict(zip(['name', 'category_name', 'stream_id', 'stream_url', 'epg_channel_id'], row)) for row in results]
    
    def show_live_results(self, results, search_term):
        """Show live channel results with arrow navigation, keyboard shortcuts and pagination"""
        page_size = 25
        current_page = 0
        total_pages = (len(results) + page_size - 1) // page_size if results else 1

        # Pre-fetch EPG for all channels (cached channels are instant, rest fetched once)
        epg_cache = {}
        uncached = []
        for result in results:
            epg_data = self.get_now_playing_local(result['stream_id'], fetch_if_missing=False)
            if epg_data:
                epg_cache[result['stream_id']] = epg_data
            else:
                uncached.append(result)
        if uncached:
            console.clear()
            self.display_header(show_status=False)
            console.print(Panel.fit(f"Loading EPG for {len(uncached)} channels (caching for next time)...", style="dim white"))
            for result in uncached:
                console.print(f"[dim]Fetching: {result['name']}[/dim]")
                epg_data = self.get_now_playing_local(result['stream_id'], result.get('name'), fetch_if_missing=True)
                epg_cache[result['stream_id']] = epg_data

        while True:
            console.clear()
            
            # Display header with page info
            if total_pages > 1:
                console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found) - Page {current_page + 1}/{total_pages}", style="dim white"))
            else:
                console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found)", style="dim white"))
            
            console.print("[dim white]# (p)lay  (r)restream to NGINX  (c)record now  (t)schedule rec  |  (s)★star  (d)✕unstar  (i)info  |  ESC=back[/dim white]\n")

            # Get current favorites for checking
            favorites_set = self.get_favorites_set()

            # Calculate page boundaries
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, len(results))
            page_results = results[start_idx:end_idx]

            # Create menu options from current page results
            options = []

            # Add Previous Page option if not on first page
            if current_page > 0:
                options.append("← Previous Page")

            # Add current page items
            for result in page_results:
                is_fav = (result.get('stream_id'), 'live') in favorites_set
                fav_indicator = "⭐ " if is_fav else "   "
                epg = epg_cache.get(result['stream_id'])
                now_playing = epg['title'] if epg and epg.get('title') else ""
                if now_playing:
                    option = f"{fav_indicator}{result['name']}  —  {now_playing}"
                else:
                    option = f"{fav_indicator}{result['name']}"
                options.append(option)
            
            # Add Next Page option if not on last page
            if current_page < total_pages - 1:
                options.append("Next Page →")
            
            options.append("Back to Search")

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "p", "i", "r", "c", "s", "d", "t"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None:  # Escape pressed
                break
            
            selected_option = options[choice] if 0 <= choice < len(options) else None
            
            # Handle navigation options
            if selected_option == "← Previous Page":
                current_page -= 1
                continue
            elif selected_option == "Next Page →":
                current_page += 1
                continue
            elif selected_option == "Back to Search":
                break
            
            # Calculate actual result index accounting for navigation options
            result_offset = 1 if current_page > 0 else 0  # Account for "Previous Page" option
            result_idx = choice - result_offset
            
            if 0 <= result_idx < len(page_results):
                selected = page_results[result_idx]
                
                # Handle shortcuts
                if chosen_key == 'p':  # Play directly
                    self.play_with_mpv(selected)
                    continue  # Stay in menu after playing
                    
                elif chosen_key == 'i':  # Show information screen
                    self.show_live_stream_info(selected)
                    continue  # Return to menu after viewing info
                    
                elif chosen_key == 'r':  # Restream directly
                    self.quick_restream(selected)
                    continue  # Stay in menu after restreaming
                    
                elif chosen_key == 'c':  # Download (changed from Copy URL)
                    self.download_live_to_data(selected)
                    continue  # Stay in menu after downloading
                    
                elif chosen_key == 's':  # Star / Save to favorites
                    result = self.save_to_favorites(selected, 'live')
                    if result == -1:
                        console.print("[yellow]⚠[/yellow] Already starred!")
                    elif result > 0:
                        console.print(f"[green]★[/green] [bright_cyan]Added to your starred list![/bright_cyan] ({result} total)")
                        console.print(f"[dim]  Playlist URL: http://localhost:8080/iptv.m3u[/dim]")
                        console.print(f"[dim]  Import in VLC, Kodi, or your TV app[/dim]")
                    else:
                        console.print("[red]✗[/red] Failed to star")
                    console.print("Press any key to continue...")
                    input()
                    continue  # Refresh menu immediately

                elif chosen_key == 'd':  # Unstar / Delete from favorites
                    result = self.remove_from_favorites(selected, 'live')
                    if result:
                        console.print(f"[green]✓[/green] Removed from starred list")
                    else:
                        console.print("[yellow]⚠[/yellow] Not in starred list")
                    console.print("Press any key to continue...")
                    input()
                    continue  # Refresh menu immediately

                elif chosen_key == 't':  # Schedule recording (Timer)
                    self.schedule_recording(selected)
                    continue  # Stay in menu after scheduling

                else:  # Enter key - show action menu
                    self.channel_action_menu(selected)
    
    def live_stream_action_menu(self, channel):
        """Menu for live stream actions"""
        while True:
            console.clear()
            console.print(Panel.fit(f"Live Stream: {channel['name']}", style="dim white"))
            console.print(f"Category: {channel['category_name'] or 'Unknown'}")
            console.print(f"Stream ID: {channel['stream_id']}")
            console.print()
            
            options = [
                "Watch Now  (opens MPV player)",
                "Stream Info  (EPG, category, ID)",
                "Restream to NGINX-RTMP",
                "Schedule Recording  (save to Samba share)",
                "★ Star  (add to playlist)",
                "Copy Stream URL",
                "Back"
            ]

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )

            choice = terminal_menu.show()

            if choice is None or choice == 6:  # Back
                break
            elif choice == 0:  # Watch
                self.play_with_mpv(channel)
            elif choice == 1:  # Info
                self.show_live_stream_info(channel)
            elif choice == 2:  # Restream
                self.quick_restream(channel)
            elif choice == 3:  # Schedule Recording
                self.schedule_recording(channel)
            elif choice == 4:  # Star / Save to Favorites
                result = self.save_to_favorites(channel, 'live')
                if result == -1:
                    console.print("[yellow]⚠[/yellow] Already starred!")
                elif result > 0:
                    console.print(f"[green]★[/green] [bright_cyan]Added to your starred list![/bright_cyan] ({result} total)")
                    console.print(f"[dim]  Playlist URL: http://localhost:8080/iptv.m3u[/dim]")
                    console.print(f"[dim]  Import in VLC, Kodi, or your TV app[/dim]")
                else:
                    console.print("[red]✗[/red] Failed to star")
                self.wait_for_escape()
            elif choice == 5:  # Copy URL
                self.copy_stream_url(channel)
    
    def vod_action_menu(self, vod_item):
        """Menu for VOD actions"""
        while True:
            console.clear()
            console.print(Panel.fit(f"VOD: {vod_item['name']}", style="dim white"))
            if vod_item.get('year'):
                console.print(f"Year: {vod_item['year']}")
            if vod_item.get('rating'):
                console.print(f"Rating: {vod_item['rating']:.1f}/10")
            if vod_item.get('genre'):
                console.print(f"Genre: {vod_item['genre']}")
            console.print()
            
            options = [
                "Watch Now  (opens MPV player)",
                "Download to Disk",
                "Movie Info  (rating, genre, description)",
                "Restream to NGINX-RTMP",
                "★ Star  (add to playlist)",
                "Copy Stream URL",
                "Back"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == 6:  # Back
                break
            elif choice == 0:  # Watch
                self.play_with_mpv({'name': vod_item['name'], 'stream_url': vod_item['stream_url']})
            elif choice == 1:  # Download
                self.download_vod(vod_item)
            elif choice == 2:  # Info
                self.show_vod_info(vod_item)
            elif choice == 3:  # Restream
                self.quick_restream(vod_item)
            elif choice == 4:  # Star / Save to Favorites
                result = self.save_to_favorites(vod_item, 'vod')
                if result == -1:
                    console.print("[yellow]⚠[/yellow] Already starred!")
                elif result > 0:
                    console.print(f"[green]★[/green] [bright_cyan]Added to your starred list![/bright_cyan] ({result} total)")
                    console.print(f"[dim]  Playlist URL: http://localhost:8080/iptv.m3u[/dim]")
                    console.print(f"[dim]  Import in VLC, Kodi, or your TV app[/dim]")
                else:
                    console.print("[red]✗[/red] Failed to star")
                self.wait_for_escape()
            elif choice == 5:  # Copy URL
                self.copy_stream_url({'stream_url': vod_item['stream_url']})
    
    def channel_action_menu(self, channel):
        """Legacy menu for channel actions - redirect to new live stream menu"""
        self.live_stream_action_menu(channel)
    
    def search_vod_menu(self):
        """Search VOD content menu"""
        if not self.check_database():
            return
        
        console.clear()
        console.print(Panel.fit("Search VOD Content", style="dim white"))
        
        try:
            search_term = input("\nEnter search term: ").strip()
            if not search_term:
                return
            
            results = self.search_vod_content(search_term)
            if not results:
                console.print(f"\nNo VOD content found for '{search_term}'")
                self.wait_for_escape()
                return
            
            self.show_vod_results(results, search_term)
            
        except KeyboardInterrupt:
            return
    
    def search_vod_content(self, query):
        """Search VOD content in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Tokenize search query for multi-word partial matching
        tokens = query.split()
        if not tokens:
            conn.close()
            return []

        # Build WHERE clause with AND for each token
        conditions = ' AND '.join(['name LIKE ?' for _ in tokens])
        params = [f'%{token}%' for token in tokens]

        sql = f"""
            SELECT stream_id, name, year, rating, genre, stream_url
            FROM vod_streams
            WHERE {conditions}
            ORDER BY CAST(rating AS REAL) DESC, name
            LIMIT 50
        """

        results = cursor.execute(sql, params).fetchall()
        conn.close()

        return [dict(zip(['stream_id', 'name', 'year', 'rating', 'genre', 'stream_url'], row)) for row in results]

    def search_series_content(self, query):
        """Search series (TV shows) in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Tokenize search query for multi-word partial matching
        tokens = query.split()
        if not tokens:
            conn.close()
            return []

        # Build WHERE clause with AND for each token
        conditions = ' AND '.join(['name LIKE ?' for _ in tokens])
        params = [f'%{token}%' for token in tokens]

        sql = f"""
            SELECT series_id, name, genre, rating, plot, category_name
            FROM series_streams
            WHERE {conditions}
            ORDER BY CAST(rating AS REAL) DESC, name
            LIMIT 50
        """

        results = cursor.execute(sql, params).fetchall()
        conn.close()

        return [dict(zip(['series_id', 'name', 'genre', 'rating', 'plot', 'category_name'], row)) for row in results]

    def show_vod_results(self, results, search_term):
        """Show VOD results with arrow navigation and pagination"""
        page_size = 25
        current_page = 0
        total_pages = (len(results) + page_size - 1) // page_size if results else 1
        
        while True:
            console.clear()
            
            # Display header with page info
            if total_pages > 1:
                console.print(Panel.fit(f"VOD Content: '{search_term}' ({len(results)} found) - Page {current_page + 1}/{total_pages}", style="dim white"))
            else:
                console.print(Panel.fit(f"VOD Content: '{search_term}' ({len(results)} found)", style="dim white"))
            
            console.print("[dim white]# (p)lay  (c)download to disk  (r)restream to NGINX  |  (s)★star  (d)✕unstar  (i)info  |  ESC=back[/dim white]\n")

            # Get current favorites for checking
            favorites_set = self.get_favorites_set()

            # Calculate page boundaries
            start_idx = current_page * page_size
            end_idx = min(start_idx + page_size, len(results))
            page_results = results[start_idx:end_idx]

            # Create menu options from current page results
            options = []

            # Add Previous Page option if not on first page
            if current_page > 0:
                options.append("← Previous Page")
            
            # Add current page items
            for result in page_results:
                # Extract year from name if not in year field
                year_match = re.search(r'\((\d{4})\)', result['name'])
                if year_match:
                    year = year_match.group(1)
                    # Remove year from display name
                    display_name = re.sub(r'\s*\(\d{4}\)\s*', '', result['name'])
                else:
                    year = result.get('year') or 'N/A'
                    display_name = result['name']
                
                # Format rating without star and decimal if .0
                if result.get('rating'):
                    # Convert to int if it's a whole number, otherwise keep 1 decimal
                    rating_val = result['rating']
                    if rating_val == int(rating_val):
                        rating = str(int(rating_val))
                    else:
                        rating = f"{rating_val:.1f}"
                else:
                    rating = 'N/A'
                
                # Use category_name as genre since genre field is empty
                if result.get('category_name'):
                    # Clean up category name to make it shorter
                    category = result['category_name']
                    # Extract main part (e.g., "FR - ACTION" -> "FR-ACTION")
                    if ' - ' in category:
                        parts = category.split(' - ')
                        genre = f"{parts[0][:2]}-{parts[1][:8]}" if len(parts) > 1 else parts[0][:12]
                    else:
                        genre = category[:12]
                else:
                    genre = result.get('genre', 'Unknown')[:12] if result.get('genre') else 'Unknown'
                
                # Store full category for favorites
                if not result.get('category_name'):
                    result['category_name'] = f"VOD/{genre}"
                    
                is_fav = (result.get('stream_id'), 'vod') in favorites_set
                fav_indicator = "⭐ " if is_fav else "   "
                
                # Ultra-compact format to avoid truncation
                # Remove prefixes like "EN -", "FR -", "NF -" from display
                clean_name = display_name
                if ' - ' in clean_name:
                    parts = clean_name.split(' - ', 1)
                    if len(parts[0]) <= 3:  # If prefix is short (EN, FR, NF, etc.)
                        clean_name = parts[1] if len(parts) > 1 else clean_name
                
                # Format: score year title (full title)
                if year != 'N/A':
                    option = f"{fav_indicator}{rating:>3} {year} {clean_name}"
                else:
                    option = f"{fav_indicator}{rating:>3} {clean_name}"
                options.append(option)
            
            # Add Next Page option if not on last page
            if current_page < total_pages - 1:
                options.append("Next Page →")
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "s", "d", "i", "r", "c", "p"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None:  # Escape pressed
                break
            
            selected_option = options[choice] if 0 <= choice < len(options) else None
            
            # Handle navigation options
            if selected_option == "← Previous Page":
                current_page -= 1
                continue
            elif selected_option == "Next Page →":
                current_page += 1
                continue
            elif selected_option == "Back to Search":
                break
            
            # Calculate actual result index accounting for navigation options
            result_offset = 1 if current_page > 0 else 0  # Account for "Previous Page" option
            result_idx = choice - result_offset
            
            if 0 <= result_idx < len(page_results):
                selected = page_results[result_idx]
                
                # Handle shortcuts
                if chosen_key == 's':  # Star / Save to favorites
                    result = self.save_to_favorites(selected, 'vod')
                    if result == -1:
                        console.print("[yellow]⚠[/yellow] Already starred!")
                        self.wait_for_escape()
                    elif result > 0:
                        console.print(f"[green]★[/green] [bright_cyan]Added to your starred list![/bright_cyan] ({result} total)")
                        console.print(f"[dim]  Playlist URL: http://localhost:8080/iptv.m3u[/dim]")
                        console.print(f"[dim]  Import in VLC, Kodi, or your TV app[/dim]")
                        self.wait_for_escape()
                    continue

                elif chosen_key == 'd':  # Unstar / Delete from favorites
                    result = self.remove_from_favorites(selected, 'vod')
                    if result > 0:
                        console.print(f"[green]✓[/green] Removed from starred list ({result} remaining)")
                        self.wait_for_escape()
                    else:
                        console.print("[yellow]⚠[/yellow] Not in starred list")
                        self.wait_for_escape()
                    continue
                    
                elif chosen_key == 'i':  # Show info
                    self.show_vod_info(selected)
                    continue
                    
                elif chosen_key == 'r':  # Restream
                    self.quick_restream(selected)
                    continue
                    
                elif chosen_key == 'c':  # Download
                    self.download_vod_to_data(selected)
                    continue
                    
                elif chosen_key == 'p':  # Play
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
                    continue
                    
                else:  # Enter key - show action menu or play
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
    
    def view_favorites_menu(self):
        """Browse starred channels and movies"""
        favs = self.load_favorites()
        if not favs:
            console.clear()
            self.display_header(show_status=False)
            console.print(Panel.fit(
                "Your starred list is empty.\n\n"
                "Star channels and movies with the [bold](s)[/bold] key while searching.\n"
                "Starred items are saved to a playlist you can import in your TV app.",
                style="dim white"
            ))
            self.wait_for_escape()
            return

        def _normalize(f):
            """Ensure favorites have the field names the result views expect."""
            n = dict(f)
            if 'category_name' not in n:
                n['category_name'] = n.get('category', 'Uncategorized')
            if 'stream_url' not in n:
                n['stream_url'] = n.get('url', '')
            return n

        live_favs = [_normalize(f) for f in favs if f.get('type') == 'live']
        vod_favs  = [_normalize(f) for f in favs if f.get('type') == 'vod']

        while True:
            console.clear()
            self.display_header(show_status=False)
            console.print(Panel.fit(
                f"[bold]My Starred List[/bold]  ({len(favs)} items)\n"
                f"[dim]Playlist URL: http://localhost:8080/iptv.m3u  —  Import in VLC, Kodi, or your TV app[/dim]",
                style="bright_cyan"
            ))

            options = []
            if live_favs:
                options.append(f"Live Channels  ({len(live_favs)} starred)")
            if vod_favs:
                options.append(f"Movies & VOD  ({len(vod_favs)} starred)")
            options.append("Back")

            choice = TerminalMenu(options, menu_cursor="> ").show()

            if choice is None or options[choice] == "Back":
                break

            label = options[choice]
            if label.startswith("Live Channels"):
                self.show_live_results(live_favs, "Starred Live Channels")
                # Reload in case user starred/unstarred items
                favs = self.load_favorites()
                live_favs = [_normalize(f) for f in favs if f.get('type') == 'live']
                vod_favs  = [_normalize(f) for f in favs if f.get('type') == 'vod']
            elif label.startswith("Movies"):
                self.show_vod_results(vod_favs, "Starred Movies & VOD")
                favs = self.load_favorites()
                live_favs = [_normalize(f) for f in favs if f.get('type') == 'live']
                vod_favs  = [_normalize(f) for f in favs if f.get('type') == 'vod']

            if not favs:
                break

    def browse_categories_menu(self):
        """Discovery Hub - Browse and explore content"""
        if not self.check_database():
            return
        
        console.clear()
        console.print(Panel.fit("🔍 Discovery Hub", style="bright_cyan"))
        console.print()
        console.print("[dim]Explore your IPTV content universe[/dim]")
        console.print()
        
        options = [
            "📺 Live TV Categories",
            "🎬 VOD Categories",
            "🎯 Smart VOD Picks"
        ]
        
        terminal_menu = TerminalMenu(
            options,
            title="",
            menu_cursor="> "
        )
        
        choice = terminal_menu.show()
        
        if choice == 0:
            self.show_live_categories()
        elif choice == 1:
            self.show_vod_categories()
        elif choice == 2:
            self.smart_vod_picks_menu()
    
    def smart_vod_picks_menu(self):
        """Smart VOD recommendations menu"""
        if not self.check_database():
            return
        
        while True:
            console.clear()
            console.print(Panel.fit("🎯 Smart VOD Picks", style="bright_cyan"))
            console.print()
            console.print("[dim]Find the perfect movie based on your preferences[/dim]")
            console.print()
            
            options = [
                "EN Top Rated English Movies (7.0+)",
                "FR Top Rated French Movies (7.0+)",
                "Netflix Originals",
                "Must Watch (9.0+ Rating)",
                "Recent Highly Rated (2018+, 7.5+)",
                "I'm Feeling Lucky (Random Pick)"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC
                break
            elif choice == 0:  # Top English
                results = self.get_smart_recommendations(languages=['EN'], min_rating=7.0, include_netflix=False, limit=40)
                if results:
                    self.show_vod_results(results, "English Movies 7.0+")
                else:
                    console.print("[yellow]No English movies found with rating 7.0+[/yellow]")
                    self.wait_for_escape()
            elif choice == 1:  # Top French
                results = self.get_smart_recommendations(languages=['FR'], min_rating=7.0, include_netflix=False, limit=40)
                if results:
                    self.show_vod_results(results, "French Movies 7.0+")
                else:
                    console.print("[yellow]No French movies found with rating 7.0+[/yellow]")
                    self.wait_for_escape()
            elif choice == 2:  # Netflix
                results = self.get_smart_recommendations(languages=['NETFLIX'], min_rating=6.0, include_netflix=True, limit=40)
                if results:
                    self.show_vod_results(results, "Netflix Originals")
                else:
                    console.print("[yellow]No Netflix content found[/yellow]")
                    self.wait_for_escape()
            elif choice == 3:  # Must Watch 9.0+
                results = self.get_smart_recommendations(languages=['EN', 'FR'], min_rating=9.0, include_netflix=True, limit=25)
                if results:
                    self.show_vod_results(results, "Must Watch 9.0+")
                else:
                    console.print("[yellow]No movies found with rating 9.0+[/yellow]")
                    self.wait_for_escape()
            elif choice == 4:  # Recent Highly Rated
                results = self.get_smart_recommendations(languages=['EN', 'FR'], min_rating=7.5, include_netflix=True, year_after=2018, limit=40)
                if results:
                    self.show_vod_results(results, "Recent Highly Rated 2018+")
                else:
                    console.print("[yellow]No recent highly rated movies found[/yellow]")
                    self.wait_for_escape()
            elif choice == 5:  # Random Pick
                import random
                results = self.get_smart_recommendations(languages=['EN', 'FR'], min_rating=7.5, include_netflix=True, limit=200)
                if results:
                    random_pick = [random.choice(results)]
                    self.show_vod_results(random_pick, "Your Lucky Pick")
                else:
                    console.print("[yellow]No movies available for random selection[/yellow]")
                    self.wait_for_escape()
    
    def show_live_categories(self):
        """Show live TV categories"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        sql = """
            SELECT category_name, COUNT(*) as count
            FROM live_streams 
            WHERE category_name IS NOT NULL
            GROUP BY category_name
            ORDER BY count DESC, category_name
            LIMIT 30
        """
        
        results = cursor.execute(sql).fetchall()
        conn.close()
        
        if not results:
            console.print("No categories found")
            self.wait_for_escape()
            return
        
        console.clear()
        console.print(Panel.fit("Live TV Categories", style="dim white"))
        
        options = [f"{row[0]} ({row[1]} channels)" for row in results]
        options.append("Back")
        
        terminal_menu = TerminalMenu(
            options,
            title="",
            menu_cursor="> "
        )
        
        choice = terminal_menu.show()
        
        if choice is not None and choice < len(results):
            category_name = results[choice][0]
            self.show_category_channels(category_name)
    
    def show_category_channels(self, category_name):
        """Show channels in a specific category"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        sql = """
            SELECT name, stream_id, stream_url, category_name, epg_channel_id
            FROM live_streams 
            WHERE category_name = ?
            ORDER BY name
            LIMIT 100
        """
        
        results = cursor.execute(sql, (category_name,)).fetchall()
        conn.close()
        
        if not results:
            return
        
        channels = [dict(zip(['name', 'stream_id', 'stream_url', 'category_name', 'epg_channel_id'], row)) for row in results]
        self.show_live_results(channels, f"Category: {category_name}")
    
    def settings_menu(self):
        """Settings menu"""
        while True:
            console.clear()
            console.print(Panel.fit("Settings", style="dim white"))

            options = [
                "YouTube",
                "Refresh Channel List",
                "──────────────────────",
                f"Inject Server URL: {self.inject_server or 'Not set'}",
                "Test MPV Installation",
                "Database Information",
                "──────────────────────",
                "Streaming Infrastructure"
            ]

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )

            choice = terminal_menu.show()

            if choice is None:
                break

            selected = options[choice]
            if selected.startswith("──"):
                continue
            elif "YouTube" in selected:
                self.youtube_tool_menu()
            elif "Refresh Channel List" in selected:
                self.download_menu()
            elif "Inject Server URL" in selected:
                self.set_inject_server()
            elif "Test MPV" in selected:
                self.test_mpv()
            elif "Database Information" in selected:
                self.show_database_info()
            elif "Streaming Infrastructure" in selected:
                self.streaming_infrastructure_menu()
    
    def set_inject_server(self):
        """Set inject server URL"""
        try:
            url = input(f"\nCurrent inject server: {self.inject_server or 'None'}\nEnter new URL (or Enter to skip): ").strip()
            if url:
                self.inject_server = url
                console.print(f"Inject server set to: {url}")
            self.wait_for_escape()
        except KeyboardInterrupt:
            pass
    
    def test_mpv(self):
        """Test MPV installation"""
        try:
            result = subprocess.run(['mpv', '--version'], capture_output=True, check=True, timeout=5)
            console.print("\nMPV is installed and working:")
            console.print(result.stdout.decode()[:200] + "...")
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("\nMPV not found. Install with:")
            console.print("Ubuntu/Debian: sudo apt install mpv")
            console.print("macOS: brew install mpv")
        except subprocess.TimeoutExpired:
            console.print("\nMPV installation test timed out")
        
        self.wait_for_escape()
    
    def show_database_info(self):
        """Show database information"""
        if not os.path.exists(self.db_path):
            console.print("\nDatabase not found")
            self.wait_for_escape()
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get table info
            tables = cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            
            console.print(f"\nDatabase: {self.db_path}")
            console.print(f"Size: {os.path.getsize(self.db_path) / 1024 / 1024:.2f} MB")
            console.print(f"Tables: {len(tables)}")
            
            for table in tables:
                count = cursor.execute(f"SELECT COUNT(*) FROM {table[0]}").fetchone()[0]
                console.print(f"  {table[0]}: {count:,} rows")
            
            conn.close()
        except Exception as e:
            console.print(f"\nError reading database: {e}")
        
        self.wait_for_escape()
    
    def check_database(self):
        """Check if database exists"""
        if not os.path.exists(self.db_path):
            console.print(Panel("Database not found. Use 'Download/Update Database' first.", style="red"))
            self.wait_for_escape()
            return False
        return True
    
    def play_with_mpv(self, channel):
        """Play channel with MPV"""
        console.clear()
        console.print(Panel.fit(f"Playing: {channel['name']}", style="dim white"))
        
        try:
            # Test MPV availability
            result = subprocess.run(['mpv', '--version'], capture_output=True, check=True, timeout=5)
            console.print("[green]✓[/green] MPV is available")
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("[red]✗[/red] MPV not found. Install MPV to play streams.")
            console.print("\nInstall instructions:")
            console.print("Ubuntu/Debian: sudo apt install mpv")
            console.print("macOS: brew install mpv")
            console.print(f"\nStream URL: {channel['stream_url']}")
            self.wait_for_escape()
            return
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] MPV check timed out, trying to play anyway...")
        
        console.print(f"Stream URL: {channel['stream_url']}")
        console.print()
        
        try:
            console.print("Starting MPV player...")
            console.print("[dim]Press 'q' in MPV window to stop playback[/dim]")
            console.print()

            # Create MPV command with enhanced buffering and reliability settings
            mpv_cmd = ['mpv', '--no-ytdl']

            # Add platform-specific hardware acceleration
            if self.is_raspberry_pi():
                # Raspberry Pi 4 with Wayland
                mpv_cmd.extend([
                    '--hwdec=v4l2m2m',                   # Pi 4 hardware decoder
                    '--vo=dmabuf-wayland',               # Native Wayland output
                    '--gpu-context=wayland',             # Wayland GPU context
                ])
            else:
                # Other platforms - auto detection
                mpv_cmd.extend([
                    '--hwdec=auto',                      # Auto hardware decoding
                    '--vo=gpu',                          # GPU video output
                ])

            # Common settings for all platforms - enhanced for IPTV stability
            mpv_cmd.extend([
                '--cache=yes',                           # Enable cache
                '--cache-secs=30',                       # Cache 30 seconds (increased)
                '--demuxer-max-bytes=100M',              # Cache up to 100MB (increased)
                '--demuxer-max-back-bytes=50M',          # Backward cache 50MB (increased)
                '--demuxer-readahead-secs=20',           # Read ahead 20 seconds
                '--network-timeout=60',                  # 60 second timeout
                # Auto-reconnect options - critical for IPTV streams that drop
                '--stream-lavf-o=reconnect=1',           # Enable reconnection
                '--stream-lavf-o=reconnect_at_eof=1',    # Reconnect on EOF
                '--stream-lavf-o=reconnect_streamed=1',  # Reconnect streaming protocols
                '--stream-lavf-o=reconnect_delay_max=30', # Max 30s retry delay
                '--keep-open=yes',                       # Keep window open
                '--osd-level=1',                         # Show OSD messages
                channel['stream_url']
            ])

            # Start MPV process - fully detached to prevent pipe deadlock
            # Using DEVNULL prevents pipe buffer from filling up and causing freeze
            process = subprocess.Popen(
                mpv_cmd,
                stdin=subprocess.DEVNULL,      # MPV can't interfere with our stdin
                stdout=subprocess.DEVNULL,     # Discard output (no pipe = no deadlock)
                stderr=subprocess.DEVNULL,     # Discard errors (no pipe = no deadlock)
                start_new_session=True         # Complete process separation
            )

            console.print(f"[green]✓[/green] MPV started with PID: {process.pid}")
            console.print()
            console.print("[bold]MPV Keyboard Controls:[/bold]")
            console.print("  q - Quit playback")
            console.print("  f - Toggle fullscreen")
            console.print("  Space - Pause/Resume")
            console.print("  → / ← - Seek forward/backward 5 seconds")
            console.print("  ↑ / ↓ - Seek forward/backward 60 seconds")
            console.print("  [ / ] - Decrease/Increase playback speed")
            console.print()
            console.print("[yellow]Press ESC in this terminal to return to menu[/yellow]")
            console.print("[dim](MPV will continue playing in the background)[/dim]")
            console.print()

            self.wait_for_escape()

            # Check if MPV is still running after user pressed ESC
            if process.poll() is None:
                console.print()
                console.print("[dim]MPV is still playing in the background.[/dim]")
                console.print("[dim]Close the MPV window or press 'q' in MPV to stop.[/dim]")
                import time
                time.sleep(1.5)
            
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to start MPV: {e}")
            console.print(f"\nTry running manually: mpv '{channel['stream_url']}'")
            self.wait_for_escape()
    
    def stream_to_inject_server(self, channel):
        """Stream to inject server"""
        if not self.inject_server:
            console.print("\nInject server not configured. Set it in Settings.")
            self.wait_for_escape()
            return
        
        console.print(f"\nStreaming {channel['name']} to inject server...")
        console.print("This feature needs implementation based on your inject server API")
        self.wait_for_escape()
    
    def copy_stream_url(self, channel):
        """Copy stream URL to clipboard"""
        console.print(f"\nStream URL: {channel['stream_url']}")
        try:
            # Try to copy to clipboard
            subprocess.run(['xclip', '-selection', 'clipboard'], input=channel['stream_url'].encode(), check=True)
            console.print("URL copied to clipboard")
        except:
            console.print("Copy to clipboard manually")
        
        self.wait_for_escape()
    
    def show_live_stream_info(self, channel):
        """Show detailed live stream information"""
        console.clear()
        console.print(Panel.fit("Live Stream Information", style="dim white"))
        
        table = Table(show_header=False, box=None)
        table.add_column("Property", style="dim white")
        table.add_column("Value", style="white")
        
        table.add_row("Name", channel['name'])
        table.add_row("Category", channel['category_name'] or 'Unknown')
        table.add_row("Stream ID", str(channel['stream_id']))
        table.add_row("Stream URL", channel['stream_url'])
        
        console.print(table)
        
        # Fetch and display EPG data
        # Always try to fetch EPG using stream_id and channel name
        console.print("\n[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]")
        console.print("[yellow]Fetching EPG data...[/yellow]")
        
        epg_listings = self.get_epg_data(channel['stream_id'], channel_name=channel.get('name'))
        
        if epg_listings:
            console.print("[green]✓[/green] EPG data available\n")
            
            # Display current and upcoming programs
            for i, program in enumerate(epg_listings[:3]):
                # Get raw values
                title_raw = program.get('title', 'Unknown Program')
                start_time = program.get('start', '')
                end_time = program.get('end', '')
                description_raw = program.get('description', '')
                
                # Decode base64 if needed
                try:
                    # Try to decode title if it looks like base64
                    if title_raw and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in title_raw.strip()):
                        title = base64.b64decode(title_raw).decode('utf-8', errors='ignore')
                    else:
                        title = title_raw
                except:
                    title = title_raw
                
                try:
                    # Try to decode description if it looks like base64
                    if description_raw and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in description_raw.strip()):
                        description = base64.b64decode(description_raw).decode('utf-8', errors='ignore')
                    else:
                        description = description_raw
                except:
                    description = description_raw
                
                if i == 0:
                    console.print("[bright_yellow]NOW PLAYING:[/bright_yellow]")
                elif i == 1:
                    console.print("\n[cyan]UP NEXT:[/cyan]")
                else:
                    console.print()
                
                if start_time and end_time:
                    # Format times - convert from UTC to local timezone
                    try:
                        from datetime import datetime, timezone
                        # Parse as UTC datetime (API returns UTC times)
                        start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                        end_dt = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                        # Convert to local timezone
                        start_local = start_dt.astimezone()
                        end_local = end_dt.astimezone()
                        time_str = f"{start_local.strftime('%H:%M')} - {end_local.strftime('%H:%M')}"
                    except:
                        time_str = f"{start_time} - {end_time}"
                    
                    console.print(f"[dim white]{time_str}[/dim white] | [white]{title}[/white]")
                else:
                    console.print(f"[white]{title}[/white]")
                
                if description and i == 0:  # Show description only for current program
                    console.print(f"[dim white]{description[:150]}...[/dim white]" if len(description) > 150 else f"[dim white]{description}[/dim white]")
        else:
            console.print("[yellow]No EPG data available for this channel[/yellow]")
        
        self.wait_for_escape()
    
    def show_vod_info(self, vod_item):
        """Show detailed VOD information"""
        console.clear()
        console.print(Panel.fit("VOD Information", style="dim white"))
        
        table = Table(show_header=False, box=None)
        table.add_column("Property", style="dim white")
        table.add_column("Value", style="white")
        
        table.add_row("Name", vod_item['name'])
        if vod_item.get('year'):
            table.add_row("Year", str(vod_item['year']))
        if vod_item.get('rating'):
            table.add_row("Rating", f"{vod_item['rating']:.1f}/10")
        if vod_item.get('genre'):
            table.add_row("Genre", vod_item['genre'])
        table.add_row("Stream URL", vod_item['stream_url'])
        
        console.print(table)
        console.print("\n[dim white]Additional metadata not available[/dim white]")
        self.wait_for_escape()

    def show_series_info(self, series_item):
        """Show detailed series information"""
        console.clear()
        console.print(Panel.fit("Series Information", style="dim white"))

        table = Table(show_header=False, box=None)
        table.add_column("Property", style="dim white")
        table.add_column("Value", style="white")

        table.add_row("Name", series_item['name'])
        if series_item.get('rating'):
            table.add_row("Rating", f"{series_item['rating']:.1f}/10")
        if series_item.get('genre'):
            table.add_row("Genre", series_item['genre'])
        if series_item.get('category_name'):
            table.add_row("Category", series_item['category_name'])
        table.add_row("Series ID", str(series_item.get('series_id', 'N/A')))

        console.print(table)

        if series_item.get('plot'):
            console.print("\n[dim white]Plot:[/dim white]")
            console.print(series_item['plot'])

        console.print("\n[dim yellow]Note: Episode browsing coming soon. Use your TV app to browse episodes.[/dim yellow]")
        self.wait_for_escape()

    def download_live_to_data(self, live_item):
        """Download/Record live stream to USB records folder (Samba share), fallback to data/"""
        console.clear()
        console.print(Panel.fit(f"Download Live: {live_item['name']}", style="dim white"))

        # Try USB_RECORDS_PATH first (Samba share), fall back to data/
        records_path = os.getenv('USB_RECORDS_PATH', '')
        if records_path and os.path.exists(records_path) and os.access(records_path, os.W_OK):
            save_folder = records_path
            console.print(f"[green]✓[/green] Saving to USB/Samba share: {save_folder}")
        else:
            save_folder = self.data_dir
            if records_path:
                console.print(f"[yellow]⚠[/yellow] USB path not available ({records_path}), using local data/ folder")
            os.makedirs(save_folder, exist_ok=True)

        # Generate filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = "".join(c for c in live_item['name'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"{safe_filename}_{timestamp}.ts".replace("  ", " ")
        filepath = os.path.join(save_folder, filename)

        console.print(f"Recording to: {filepath}")
        console.print(f"Source: {live_item['stream_url']}")
        console.print()
        
        # Use ffmpeg to record live stream
        try:
            # Check if ffmpeg is installed
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            
            # Start ffmpeg recording in background
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', live_item['stream_url'],
                '-c', 'copy',  # Copy codec, no transcoding
                '-t', '3600',  # Max 1 hour recording
                filepath
            ]
            
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            console.print(f"[green]✓[/green] Recording started (PID: {process.pid})")
            console.print("Recording for max 1 hour or press Ctrl+C in terminal to stop")
            console.print(f"File will be saved to: {filepath}")
            
        except FileNotFoundError:
            console.print("[red]✗[/red] FFmpeg not installed")
            console.print("Install with: sudo apt install ffmpeg")
        except Exception as e:
            console.print(f"[red]✗[/red] Recording failed: {e}")
        
        console.print("\n[dim white]Press any key to return...[/dim white]")
        input()
    
    def download_vod_to_data(self, vod_item):
        """Download VOD content to data folder (simplified version for shortcut)"""
        console.clear()
        console.print(Panel.fit(f"Download: {vod_item['name']}", style="dim white"))
        
        # Create data folder if it doesn't exist
        import os
        data_folder = "data"
        if not os.path.exists(data_folder):
            os.makedirs(data_folder)
            console.print(f"[green]✓[/green] Created {data_folder} folder")
        
        # Generate filename
        safe_filename = "".join(c for c in vod_item['name'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"{safe_filename}.mp4".replace("  ", " ")
        filepath = os.path.join(data_folder, filename)
        
        console.print(f"Downloading to: {filepath}")
        console.print(f"Source: {vod_item['stream_url']}")
        console.print()
        
        # Determine download method priority: wget > curl > python-requests
        download_cmd = None
        try:
            subprocess.run(['wget', '--version'], capture_output=True, check=True)
            download_cmd = 'wget'
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(['curl', '--version'], capture_output=True, check=True)
                download_cmd = 'curl'
            except (subprocess.CalledProcessError, FileNotFoundError):
                download_cmd = 'python'
        
        console.print(f"Using: {download_cmd}")
        console.print()
        
        try:
            if download_cmd == 'wget':
                # Start wget in background
                process = subprocess.Popen([
                    'wget', 
                    '-O', filepath,
                    '--user-agent=VLC/3.0.0 LibVLC/3.0.0',
                    '--timeout=30',
                    '--tries=3',
                    vod_item['stream_url']
                ])
                console.print(f"[green]✓[/green] Download started (PID: {process.pid})")
                
            elif download_cmd == 'curl':
                # Start curl in background
                process = subprocess.Popen([
                    'curl', 
                    '-o', filepath,
                    '-A', 'VLC/3.0.0 LibVLC/3.0.0',
                    '--connect-timeout', '30',
                    '--max-time', '0',
                    '-L',  # Follow redirects
                    vod_item['stream_url']
                ])
                console.print(f"[green]✓[/green] Download started (PID: {process.pid})")
                
            else:  # python requests
                console.print("[yellow]⚠[/yellow] Using Python requests (slower)")
                # Quick start message, then download in background
                import threading
                def download_thread():
                    try:
                        headers = {'User-Agent': 'VLC/3.0.0 LibVLC/3.0.0'}
                        response = requests.get(vod_item['stream_url'], headers=headers, stream=True, timeout=30)
                        response.raise_for_status()
                        
                        with open(filepath, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                    except Exception as e:
                        console.print(f"[red]✗[/red] Download failed: {e}")
                
                thread = threading.Thread(target=download_thread)
                thread.daemon = True
                thread.start()
                console.print("[green]✓[/green] Download started in background")
            
            console.print(f"File will be saved to: {filepath}")
            console.print("Download is running in background...")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Download failed: {e}")
        
        console.print("\n[dim white]Press any key to return to search results...[/dim white]")
        input()

    def download_vod(self, vod_item):
        """Download VOD content"""
        console.clear()
        console.print(Panel.fit(f"Download: {vod_item['name']}", style="dim white"))
        
        # Determine download method priority: wget > curl > python-requests
        download_cmd = None
        try:
            subprocess.run(['wget', '--version'], capture_output=True, check=True)
            download_cmd = 'wget'
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(['curl', '--version'], capture_output=True, check=True)
                download_cmd = 'curl'
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Fall back to Python requests
                download_cmd = 'python'
        
        # Get filename from URL or use default
        filename = vod_item['name'].replace(" ", "_").replace("/", "_") + ".mp4"
        
        console.print(f"Download tool: {download_cmd}")
        console.print(f"Filename: {filename}")
        console.print(f"URL: {vod_item['stream_url']}")
        console.print()
        
        if download_cmd == 'wget':
            console.print(f"Command: wget -O '{filename}' --user-agent='VLC/3.0.0' '{vod_item['stream_url']}'")
        elif download_cmd == 'curl':
            console.print(f"Command: curl -o '{filename}' -A 'VLC/3.0.0' '{vod_item['stream_url']}'")
        else:  # python
            console.print(f"Using Python requests with proper headers")
        
        console.print("\n[yellow]Warning: Large file download will start![/yellow]")
        
        # Simple confirmation
        try:
            key = input()
            if key == '\x1b':  # ESC key
                console.print("Download cancelled")
                self.wait_for_escape()
                return
        except:
            pass
        
        console.print("\nStarting download...")
        try:
            if download_cmd == 'wget':
                # Add headers for wget to mimic a browser/player
                process = subprocess.Popen([
                    'wget', 
                    '-O', filename,
                    '--user-agent=VLC/3.0.0 LibVLC/3.0.0',
                    '--header=Accept: */*',
                    '--header=Connection: keep-alive',
                    '--timeout=30',
                    '--tries=3',
                    vod_item['stream_url']
                ])
                console.print(f"Download started with PID: {process.pid}")
                console.print("Download is running in the background...")
                
            elif download_cmd == 'curl':
                # Add headers for curl to mimic a browser/player
                process = subprocess.Popen([
                    'curl', 
                    '-o', filename,
                    '-A', 'VLC/3.0.0 LibVLC/3.0.0',
                    '-H', 'Accept: */*',
                    '-H', 'Connection: keep-alive',
                    '--connect-timeout', '30',
                    '--max-time', '0',
                    '-L',  # Follow redirects
                    vod_item['stream_url']
                ])
                console.print(f"Download started with PID: {process.pid}")
                console.print("Download is running in the background...")
                
            else:  # python requests
                self._download_with_requests(vod_item, filename)
            
            console.print()
            console.print("Note: If download fails with authentication errors,")
            console.print("the server may require streaming-only access.")
            
        except Exception as e:
            console.print(f"[red]Download failed: {e}[/red]")
        
        self.wait_for_escape()
    
    def _download_with_requests(self, vod_item, filename):
        """Download using Python requests with proper headers"""
        import threading
        
        def download_thread():
            try:
                headers = {
                    'User-Agent': 'VLC/3.0.0 LibVLC/3.0.0',
                    'Accept': '*/*',
                    'Connection': 'keep-alive',
                    'Range': 'bytes=0-'
                }
                
                console.print("Starting Python requests download...")
                response = requests.get(vod_item['stream_url'], headers=headers, stream=True, timeout=30)
                response.raise_for_status()
                
                total_size = int(response.headers.get('content-length', 0))
                console.print(f"File size: {total_size / (1024*1024):.2f} MB" if total_size > 0 else "File size: Unknown")
                
                with open(filename, 'wb') as f:
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                console.print(f"\rDownload progress: {progress:.1f}%", end="")
                
                console.print(f"\n[green]✓[/green] Download completed: {filename}")
                
            except Exception as e:
                console.print(f"\n[red]✗[/red] Download failed: {e}")
        
        # Start download in background thread
        thread = threading.Thread(target=download_thread)
        thread.daemon = True
        thread.start()
        console.print("Download started in background thread...")

    def schedule_recording(self, live_item):
        """Schedule a recording for a live channel at a specific time"""
        console.clear()
        console.print(Panel.fit(f"Schedule Recording: {live_item['name']}", style="cyan"))
        console.print()

        # Get USB_RECORDS_PATH from environment
        records_path = os.getenv('USB_RECORDS_PATH', '/mnt/media/RECORDS')

        # Check if records path exists and is writable
        if not os.path.exists(records_path):
            console.print(f"[yellow]Warning:[/yellow] Records path does not exist: {records_path}")
            console.print("Attempting to create directory...")
            try:
                os.makedirs(records_path, exist_ok=True)
                console.print(f"[green]✓[/green] Created {records_path}")
            except Exception as e:
                console.print(f"[red]✗[/red] Cannot create records path: {e}")
                console.print("\nPlease ensure USB_RECORDS_PATH is set correctly in .env")
                self.wait_for_escape()
                return

        # Test write permission
        test_file = os.path.join(records_path, ".write_test")
        try:
            with open(test_file, 'w') as f:
                f.write("test")
            os.remove(test_file)
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Cannot write to [bold]{records_path}[/bold]")
            console.print(f"[dim]  Reason: {e}[/dim]")
            console.print()
            console.print("[dim]This usually means the drive is mounted read-only or owned by root.[/dim]")
            console.print("[dim]Fix: remount with your user's uid, or run:[/dim]")
            console.print(f"[dim]  sudo chown -R $USER {records_path}[/dim]")
            console.print()
            fallback_path = os.path.join(self.data_dir, "recordings")
            fix_menu = TerminalMenu(
                [f"Record to {fallback_path} instead  (local fallback)", "Cancel"],
                title="Samba share not writable",
                menu_cursor="> "
            )
            if fix_menu.show() == 0:
                os.makedirs(fallback_path, exist_ok=True)
                records_path = fallback_path
                console.print(f"[green]✓[/green] Using local fallback: {records_path}")
            else:
                return

        console.print(f"[green]✓[/green] Recording will be saved to: {records_path}")
        console.print()

        # Get start time from user
        console.print("[bold]Enter start date/time:[/bold]")
        console.print("[dim]Formats: 'now', '19:00', 'tomorrow 19:00', '2024-12-09 19:00'[/dim]")
        console.print()

        try:
            start_input = input("Start time: ").strip()
            if not start_input:
                console.print("[red]Cancelled[/red]")
                self.wait_for_escape()
                return
        except KeyboardInterrupt:
            return

        # Parse start time
        from dateutil import parser as dateparser
        import time

        try:
            if start_input.lower() == 'now':
                start_time = datetime.now()
            elif ':' in start_input and len(start_input) <= 5:
                # Just time like "19:00" - ALWAYS assume today
                today = datetime.now().date()
                parsed_time = datetime.strptime(start_input, "%H:%M").time()
                start_time = datetime.combine(today, parsed_time)
            elif start_input.lower().startswith('tomorrow'):
                # "tomorrow 19:00"
                time_part = start_input.lower().replace('tomorrow', '').strip()
                tomorrow = datetime.now().date() + timedelta(days=1)
                if time_part:
                    parsed_time = datetime.strptime(time_part, "%H:%M").time()
                else:
                    parsed_time = datetime.now().time()
                start_time = datetime.combine(tomorrow, parsed_time)
            else:
                # Try to parse as full datetime
                start_time = dateparser.parse(start_input)
                if not start_time:
                    raise ValueError("Could not parse date")
        except Exception as e:
            console.print(f"[red]✗[/red] Invalid date format: {e}")
            console.print("Try formats like: 'now', '19:00', 'tomorrow 19:00', '2024-12-09 19:00'")
            self.wait_for_escape()
            return

        # Check if start time is in the past (allow 1 minute tolerance for "now")
        if start_time < datetime.now() - timedelta(minutes=1):
            console.print(f"[red]✗[/red] Start time is in the past: {start_time}")
            self.wait_for_escape()
            return

        # Track if user wants to start "now" - we'll set actual time after confirmation
        start_now = start_input.lower() == 'now'

        if start_now:
            console.print(f"[green]✓[/green] Start time: NOW (immediately after confirmation)")
        else:
            console.print(f"[green]✓[/green] Start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        console.print()

        # Get duration
        console.print("[bold]Select recording duration:[/bold]")
        duration_options = [
            ("30 minutes", 30 * 60),
            ("1 hour", 60 * 60),
            ("1.5 hours", 90 * 60),
            ("2 hours", 2 * 60 * 60),
            ("2.5 hours", 150 * 60),
            ("3 hours", 3 * 60 * 60),
            ("4 hours", 4 * 60 * 60),
            ("Custom duration", None),
            ("Cancel", None)
        ]

        duration_menu = TerminalMenu(
            [opt[0] for opt in duration_options],
            title="Duration"
        )
        duration_idx = duration_menu.show()

        if duration_idx is None or duration_options[duration_idx][0] == "Cancel":
            console.print("[red]Cancelled[/red]")
            self.wait_for_escape()
            return

        if duration_options[duration_idx][1] is None:
            # Custom duration
            try:
                custom_mins = input("Enter duration in minutes: ").strip()
                duration_seconds = int(custom_mins) * 60
                if duration_seconds < 60:
                    console.print("[red]Duration must be at least 1 minute[/red]")
                    self.wait_for_escape()
                    return
            except (ValueError, KeyboardInterrupt):
                console.print("[red]Invalid duration[/red]")
                self.wait_for_escape()
                return
        else:
            duration_seconds = duration_options[duration_idx][1]

        duration_hours = duration_seconds / 3600
        console.print(f"[green]✓[/green] Duration: {duration_hours:.1f} hours ({duration_seconds} seconds)")
        console.print()

        # Generate output filename - try to use EPG program title if available
        program_title = None
        try:
            # Look up EPG for the scheduled time
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # For "now", use current time; otherwise use scheduled start_time
            lookup_time = int(datetime.now().timestamp()) if start_now else int(start_time.timestamp())
            cursor.execute('''
                SELECT title FROM epg
                WHERE stream_id = ? AND start_time <= ? AND end_time > ?
                LIMIT 1
            ''', (live_item.get('stream_id'), lookup_time, lookup_time))
            row = cursor.fetchone()
            if row and row[0]:
                program_title = row[0]
            conn.close()
        except Exception:
            pass

        # Use program title if found, otherwise channel name
        if program_title:
            base_name = program_title
            console.print(f"[green]✓[/green] EPG Program: {program_title}")
        else:
            base_name = live_item['name']
            console.print(f"[dim]No EPG data - using channel name[/dim]")

        safe_name = "".join(c for c in base_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
        safe_name = safe_name.replace(' ', '_').lower()[:50]
        date_str = start_time.strftime('%Y-%m-%d_%H%M') if not start_now else datetime.now().strftime('%Y-%m-%d_%H%M')
        output_filename = f"{safe_name}_{date_str}.ts"
        output_path = os.path.join(records_path, output_filename)

        console.print(f"Output file: [cyan]{output_path}[/cyan]")
        console.print()

        # Create systemd timer command
        # Format: systemd-run --user --on-calendar="YYYY-MM-DD HH:MM:SS" --unit=NAME COMMAND
        timer_unit = f"iptv-record-{int(time.time())}"
        calendar_str = start_time.strftime('%Y-%m-%d %H:%M:%S')

        wrapper_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "record_wrapper.sh")

        # Build the systemd-run command
        systemd_cmd = [
            "systemd-run",
            "--user",
            f"--on-calendar={calendar_str}",
            f"--unit={timer_unit}",
            "--description=IPTV Scheduled Recording",
            "/bin/bash",
            wrapper_path,
            "--stream-id", str(live_item.get('stream_id', 0)),
            "--duration", str(duration_seconds),
            "--output", output_path,
            "--channel-name", live_item['name']
        ]

        console.print("[bold]Confirm recording schedule:[/bold]")
        console.print()

        table = Table(show_header=False, box=None)
        table.add_column("Field", style="dim")
        table.add_column("Value", style="cyan")
        table.add_row("Channel", live_item['name'])
        # For "now", show that it starts immediately; otherwise show the scheduled time
        if start_now:
            table.add_row("Start", "NOW (immediately)")
        else:
            table.add_row("Start", f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} (local time)")
        table.add_row("Duration", f"{duration_hours:.1f} hours")
        if not start_now:
            table.add_row("End (approx)", (start_time + timedelta(seconds=duration_seconds)).strftime('%Y-%m-%d %H:%M:%S'))
        table.add_row("Output", output_path)
        table.add_row("Timer", timer_unit)
        console.print(table)
        console.print()

        confirm_menu = TerminalMenu(
            ["Confirm and Schedule", "Cancel"],
            title="Schedule this recording?"
        )
        confirm_idx = confirm_menu.show()

        if confirm_idx != 0:
            console.print("[red]Cancelled[/red]")
            self.wait_for_escape()
            return

        # If "now" was selected, use --on-active for relative time (more reliable)
        if start_now:
            start_time = datetime.now() + timedelta(seconds=10)
            console.print(f"[dim]Actual start time: {start_time.strftime('%Y-%m-%d %H:%M:%S')} (local)[/dim]")
            # Use --on-active for "now" which is relative (avoids timezone issues)
            systemd_cmd[2] = "--on-active=10s"
        else:
            # For scheduled times, use local timezone explicitly
            # Format: "YYYY-MM-DD HH:MM:SS TZ" - systemd needs local time specification
            local_tz = datetime.now().astimezone().strftime('%Z')  # Get local timezone like "EST"
            calendar_str = f"{start_time.strftime('%Y-%m-%d %H:%M:%S')} {local_tz}"
            console.print(f"[dim]Scheduled time: {calendar_str}[/dim]")
            systemd_cmd[2] = f"--on-calendar={calendar_str}"

        # Execute systemd-run command
        try:
            # Debug: show the command being run
            console.print(f"[dim]Running: {' '.join(systemd_cmd)}[/dim]")

            result = subprocess.run(
                systemd_cmd,
                capture_output=True,
                text=True,
                timeout=10
            )

            # Debug: show result
            console.print(f"[dim]Return code: {result.returncode}[/dim]")
            if result.stdout:
                console.print(f"[dim]stdout: {result.stdout}[/dim]")
            if result.stderr:
                console.print(f"[dim]stderr: {result.stderr}[/dim]")

            if result.returncode == 0:
                # Save to database
                try:
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO scheduled_recordings
                        (stream_id, channel_name, start_time, duration, output_path, timer_unit, status, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                    ''', (
                        live_item.get('stream_id', 0),
                        live_item['name'],
                        int(start_time.timestamp()),
                        duration_seconds,
                        output_path,
                        timer_unit,
                        int(datetime.now().timestamp())
                    ))
                    recording_id = cursor.lastrowid
                    conn.commit()
                    conn.close()
                except Exception as db_err:
                    console.print(f"[yellow]Warning: Could not save to database: {db_err}[/yellow]")
                    recording_id = None

                console.print()
                console.print(Panel.fit(
                    "[green]Recording Scheduled Successfully![/green]\n\n"
                    f"Channel: {live_item['name']}\n"
                    f"Start: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"Duration: {duration_hours:.1f} hours\n"
                    f"Output: {output_path}\n"
                    f"Timer: {timer_unit}\n\n"
                    "[dim]Recording will execute even if this app is closed.[/dim]\n"
                    "[dim]View timers: systemctl --user list-timers | grep iptv[/dim]",
                    style="green"
                ))
            else:
                console.print(f"[red]✗[/red] Failed to schedule recording")
                console.print(f"Error: {result.stderr}")

                # Check for common issues
                if "linger" in result.stderr.lower():
                    console.print()
                    console.print("[yellow]Tip: Enable lingering for your user:[/yellow]")
                    console.print(f"  sudo loginctl enable-linger {os.getenv('USER')}")

        except subprocess.TimeoutExpired:
            console.print("[red]✗[/red] Timeout scheduling recording")
        except FileNotFoundError:
            console.print("[red]✗[/red] systemd-run not found")
            console.print("This feature requires systemd (Linux)")
        except Exception as e:
            console.print(f"[red]✗[/red] Error scheduling recording: {e}")

        self.wait_for_escape()

    def view_scheduled_recordings(self):
        """View and manage scheduled recordings"""
        while True:
            console.clear()
            console.print(Panel.fit("Scheduled Recordings", style="cyan"))
            console.print()

            # Get recordings from database
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, channel_name, start_time, duration, output_path, timer_unit, status
                    FROM scheduled_recordings
                    ORDER BY start_time DESC
                    LIMIT 50
                ''')
                recordings = cursor.fetchall()
                conn.close()
            except Exception as e:
                console.print(f"[red]Error loading recordings: {e}[/red]")
                self.wait_for_escape()
                return

            if not recordings:
                console.print("[dim]No scheduled recordings found.[/dim]")
                console.print()
                console.print("To schedule a recording:")
                console.print("1. Search for a live channel")
                console.print("2. Select the channel")
                console.print("3. Choose 'Schedule Recording'")
                self.wait_for_escape()
                return

            # Build menu options
            menu_options = []
            for rec in recordings:
                rec_id, channel, start_ts, duration, output, timer, status = rec
                start_dt = datetime.fromtimestamp(start_ts)
                duration_hrs = duration / 3600

                # Status indicator
                if status == 'pending':
                    if start_dt > datetime.now():
                        status_icon = "[cyan]⏰[/cyan]"  # Scheduled future
                    else:
                        # Check if the systemd service is actually running
                        try:
                            r = subprocess.run(
                                ["systemctl", "--user", "is-active", f"{timer}.service"],
                                capture_output=True, text=True, timeout=3
                            )
                            if r.stdout.strip() == "active":
                                status_icon = "[red]●[/red]"  # Actively recording
                            else:
                                status_icon = "[yellow]?[/yellow]"  # Unknown
                        except Exception:
                            status_icon = "[yellow]?[/yellow]"
                elif status == 'recording':
                    status_icon = "[red]●[/red]"  # Recording
                elif status == 'completed':
                    status_icon = "[green]✓[/green]"  # Done
                elif status == 'cancelled':
                    status_icon = "[dim]✗[/dim]"  # Cancelled
                else:
                    status_icon = "[red]![/red]"  # Failed/unknown

                menu_options.append(
                    f"{status_icon} {channel[:30]:30} | {start_dt.strftime('%m-%d %H:%M')} | {duration_hrs:.1f}h | {status}"
                )

            menu_options.append("─" * 60)
            menu_options.append("Refresh")
            menu_options.append("Back")

            menu = TerminalMenu(
                menu_options,
                title="Select recording to view details or cancel"
            )

            idx = menu.show()

            if idx is None or menu_options[idx] == "Back":
                return
            elif menu_options[idx] == "Refresh":
                continue
            elif "─" in menu_options[idx]:
                continue
            else:
                # Show recording details
                rec = recordings[idx]
                rec_id, channel, start_ts, duration, output, timer, status = rec

                self._show_recording_details(rec_id, channel, start_ts, duration, output, timer, status)

    def _show_recording_details(self, rec_id, channel, start_ts, duration, output, timer, status):
        """Show details for a scheduled recording and allow cancellation"""
        console.clear()
        console.print(Panel.fit(f"Recording Details", style="cyan"))
        console.print()

        start_dt = datetime.fromtimestamp(start_ts)
        end_dt = start_dt + timedelta(seconds=duration)
        duration_hrs = duration / 3600

        table = Table(show_header=False, box=None)
        table.add_column("Field", style="dim", width=15)
        table.add_column("Value", style="white")

        table.add_row("ID", str(rec_id))
        table.add_row("Channel", channel)
        table.add_row("Start Time", start_dt.strftime('%Y-%m-%d %H:%M:%S'))
        table.add_row("End Time", end_dt.strftime('%Y-%m-%d %H:%M:%S'))
        table.add_row("Duration", f"{duration_hrs:.1f} hours ({duration} seconds)")
        table.add_row("Output", output)
        table.add_row("Timer Unit", timer)
        table.add_row("Status", status)

        console.print(table)
        console.print()

        # Check if file exists (for completed recordings)
        if os.path.exists(output):
            size_mb = os.path.getsize(output) / (1024 * 1024)
            console.print(f"[green]✓[/green] Output file exists: {size_mb:.1f} MB")
        elif status == 'completed':
            console.print(f"[yellow]⚠[/yellow] Output file not found")

        # Check systemd timer status
        if status == 'pending':
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "status", f"{timer}.timer"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    console.print(f"[green]✓[/green] Timer is active")
                else:
                    console.print(f"[yellow]⚠[/yellow] Timer may not be active")
            except:
                pass

        console.print()

        # Options
        options = []
        if status == 'pending':
            options.append("Stop/Cancel Recording")
        if os.path.exists(output):
            options.append("Play Recording")
            options.append("Delete Recording File")
        options.append("Back")

        menu = TerminalMenu(options, title="Actions")
        action_idx = menu.show()

        if action_idx is None or options[action_idx] == "Back":
            return
        elif options[action_idx] == "Stop/Cancel Recording":
            self._cancel_scheduled_recording(rec_id, timer)
        elif options[action_idx] == "Play Recording":
            subprocess.Popen(
                ['mpv', output],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            console.print("[green]✓[/green] Playing recording in MPV")
            self.wait_for_escape()
        elif options[action_idx] == "Delete Recording File":
            confirm = TerminalMenu(["Yes, delete", "No, keep"], title="Delete recording file?")
            if confirm.show() == 0:
                try:
                    os.remove(output)
                    console.print(f"[green]✓[/green] Deleted: {output}")
                except Exception as e:
                    console.print(f"[red]✗[/red] Could not delete: {e}")
                self.wait_for_escape()

    def _cancel_scheduled_recording(self, rec_id, timer_unit):
        """Cancel a scheduled recording"""
        console.print()
        console.print(f"Cancelling recording {rec_id}...")

        # Stop the systemd timer
        try:
            # Try to stop both timer and service
            subprocess.run(
                ["systemctl", "--user", "stop", f"{timer_unit}.timer"],
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ["systemctl", "--user", "stop", f"{timer_unit}.service"],
                capture_output=True,
                timeout=10
            )
            console.print(f"[green]✓[/green] Stopped systemd timer")
        except Exception as e:
            console.print(f"[yellow]Warning: Could not stop timer: {e}[/yellow]")

        # Update database
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE scheduled_recordings SET status = 'cancelled' WHERE id = ?",
                (rec_id,)
            )
            conn.commit()
            conn.close()
            console.print(f"[green]✓[/green] Updated database status to 'cancelled'")
        except Exception as e:
            console.print(f"[red]✗[/red] Could not update database: {e}")

        console.print()
        console.print("[green]Recording cancelled.[/green]")
        self.wait_for_escape()

    def restream_placeholder(self, item):
        """Restream content through NGINX-RTMP server"""
        console.clear()
        console.print(Panel.fit(f"Restream: {item['name']}", style="dim white"))
        
        # Check if NGINX container is running
        if "[green]" not in self.check_container_status():
            console.print("[red]✗[/red] NGINX-RTMP container is not running")
            console.print("Start the container from 'Build NGINX Container' menu first")
            self.wait_for_escape()
            return
        
        # Check FFmpeg availability
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, timeout=5)
        except:
            console.print("[red]✗[/red] FFmpeg not found. Install with:")
            console.print("Ubuntu/Debian: sudo apt install ffmpeg")
            console.print("macOS: brew install ffmpeg")
            self.wait_for_escape()
            return
        
        # Generate stream key from item name
        stream_key = self._generate_stream_key(item['name'])
        
        console.print(f"Source: {item.get('stream_url', 'N/A')}")
        console.print(f"Stream Key: {stream_key}")
        console.print(f"RTMP Target: rtmp://localhost:1935/live/{stream_key}")
        console.print(f"View URL: http://localhost:8080/hls/{stream_key}.m3u8")
        console.print()
        
        options = [
            "Start Restream",
            "Start with Transcoding (Lower Bandwidth)",
            "View Stream URLs",
            "Stop Active Restream",
            "Back"
        ]
        
        terminal_menu = TerminalMenu(
            options,
            title="",
            menu_cursor="> "
        )
        
        choice = terminal_menu.show()
        
        if choice == 0:  # Start restream
            self._start_restream(item, stream_key, transcode=False)
        elif choice == 1:  # Start with transcoding
            self._start_restream(item, stream_key, transcode=True)
        elif choice == 2:  # View URLs
            self._show_stream_urls(stream_key)
        elif choice == 3:  # Stop restream
            self._stop_restream()
    
    def _generate_stream_key(self, name):
        """Generate a stream key from content name"""
        # Clean name for use as stream key
        key = re.sub(r'[^a-zA-Z0-9_-]', '_', name.lower())
        key = re.sub(r'_+', '_', key)  # Remove multiple underscores
        return key[:50]  # Limit length

    def detect_running_restream(self):
        """Detect FFmpeg processes streaming to our RTMP server.

        This scans running processes to find FFmpeg instances pushing to
        rtmp://localhost:1935/live/ - useful when app restarts but stream continues.

        Returns dict with stream info or None if nothing found.
        """
        try:
            # Use pgrep to find ffmpeg processes, then inspect their command lines
            result = subprocess.run(
                ['pgrep', '-a', 'ffmpeg'],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                return None

            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                # Check if this FFmpeg is streaming to our RTMP server
                if 'rtmp://localhost:1935/live/' in line or 'rtmp://127.0.0.1:1935/live/' in line:
                    parts = line.split(None, 1)
                    if len(parts) < 2:
                        continue

                    pid = int(parts[0])
                    cmd = parts[1]

                    # Extract stream key from the RTMP URL
                    stream_key = None
                    source_url = None

                    # Parse command line arguments
                    import shlex
                    try:
                        args = shlex.split(cmd)
                    except:
                        args = cmd.split()

                    for i, arg in enumerate(args):
                        # Find source URL (after -i)
                        if arg == '-i' and i + 1 < len(args):
                            source_url = args[i + 1]
                        # Find RTMP target URL
                        if 'rtmp://localhost:1935/live/' in arg or 'rtmp://127.0.0.1:1935/live/' in arg:
                            # Extract stream key from URL
                            stream_key = arg.split('/live/')[-1]

                    if stream_key:
                        return {
                            "stream_key": stream_key,
                            "channel_name": f"[Recovered] {stream_key}",
                            "pid": pid,
                            "source_url": source_url or "Unknown",
                            "started_at": None,
                            "recovered": True  # Flag indicating this was detected, not tracked
                        }

            return None

        except Exception:
            return None

    def get_active_restream(self):
        """Returns dict with active restream info or None if nothing active.

        First checks our metadata file, then falls back to process detection
        in case the app crashed/restarted while a stream was running.
        """
        meta_file = os.path.join(self.data_dir, ".restream_active.json")

        # First try: check our metadata file
        if os.path.exists(meta_file):
            try:
                with open(meta_file, 'r') as f:
                    data = json.load(f)
                # Verify process is still running
                os.kill(data['pid'], 0)
                return data
            except (ProcessLookupError, json.JSONDecodeError, KeyError, FileNotFoundError):
                # Process dead or file corrupt, clean up
                try:
                    os.remove(meta_file)
                except:
                    pass

        # Second try: detect running FFmpeg processes (for crash recovery)
        detected = self.detect_running_restream()
        if detected:
            # Save the recovered metadata so we can track it properly now
            self.save_restream_meta(
                detected['stream_key'],
                detected['channel_name'],
                detected['pid'],
                detected['source_url']
            )
            return detected

        return None

    def save_restream_meta(self, stream_key, channel_name, pid, source_url):
        """Save restream metadata to JSON file"""
        meta_file = os.path.join(self.data_dir, ".restream_active.json")
        data = {
            "stream_key": stream_key,
            "channel_name": channel_name,
            "pid": pid,
            "source_url": source_url,
            "started_at": datetime.now().isoformat()
        }
        with open(meta_file, 'w') as f:
            json.dump(data, f)

    def clear_restream_meta(self):
        """Remove restream metadata file"""
        meta_file = os.path.join(self.data_dir, ".restream_active.json")
        if os.path.exists(meta_file):
            try:
                os.remove(meta_file)
            except:
                pass

    def stop_restream_quick(self):
        """Quick stop of active restream with minimal UI"""
        active = self.get_active_restream()
        if not active:
            return False

        try:
            os.kill(active['pid'], signal.SIGTERM)
            self.clear_restream_meta()
            console.print(f"[green]✓[/green] Stopped restreaming: {active['channel_name']}")
            time.sleep(1)
            return True
        except ProcessLookupError:
            self.clear_restream_meta()
            return False

    def quick_restream(self, item):
        """One-click restream - starts immediately without submenu"""
        # Check if nginx container is running
        container_status = self.check_container_status()
        if "[green]" not in container_status:
            console.print("[yellow]⚠[/yellow] NGINX-RTMP is not running.")
            console.print()
            fix_menu = TerminalMenu(
                ["Start NGINX-RTMP and then restream", "Cancel"],
                title="NGINX-RTMP is offline",
                menu_cursor="> "
            )
            if fix_menu.show() == 0:
                console.print("[dim]Starting NGINX-RTMP...[/dim]")
                start_result = subprocess.run(
                    ['docker-compose', 'up', '-d', 'nginx-rtmp'],
                    capture_output=True, timeout=60
                )
                if start_result.returncode != 0:
                    console.print("[red]✗[/red] Failed to start NGINX-RTMP")
                    self.wait_for_escape()
                    return
                # Invalidate service status cache
                self._svc_cache_time = 0
                console.print("[green]✓[/green] NGINX-RTMP started. Launching restream...")
            else:
                return

        # Check if ffmpeg is available
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("[red]✗[/red] FFmpeg not found")
            self.wait_for_escape()
            return

        channel_name = item.get('name', 'Unknown')
        source_url = item.get('stream_url')

        if not source_url:
            console.print("[red]✗[/red] No stream URL available")
            self.wait_for_escape()
            return

        # Check if already restreaming
        active = self.get_active_restream()

        if active:
            if active['channel_name'] == channel_name:
                # Same channel - show info
                console.clear()
                console.print(Panel.fit(f"Already Restreaming: {channel_name}", style="green"))
                console.print()
                console.print(f"[bright_yellow]HLS:[/bright_yellow] http://localhost:8080/hls/{active['stream_key']}.m3u8")
                console.print(f"[bright_yellow]RTMP:[/bright_yellow] rtmp://localhost:1935/live/{active['stream_key']}")
                self.wait_for_escape()
                return
            else:
                # Different channel - ask to switch
                console.print()
                console.print(f"[yellow]Currently restreaming:[/yellow] {active['channel_name']}")
                try:
                    confirm = input(f"Switch to '{channel_name}'? [Y/n]: ").strip().lower()
                except KeyboardInterrupt:
                    return
                if confirm not in ['y', 'yes', '']:
                    return
                # Stop current restream
                self.stop_restream_quick()

        # Start new restream
        stream_key = self._generate_stream_key(channel_name)
        target_url = f"rtmp://localhost:1935/live/{stream_key}"

        console.print(f"\n[dim]Starting restream...[/dim]")

        # Build FFmpeg command with reconnect and buffering for stable IPTV restreaming
        # Video: copy (no re-encoding for best quality)
        # Audio: transcode to AAC (required for RTMP/FLV - AC3/EAC3 not supported)
        ffmpeg_cmd = [
            'ffmpeg',
            # Input options - reconnect and buffering (MUST come before -i)
            '-reconnect', '1',                    # Enable reconnection
            '-reconnect_at_eof', '1',             # Reconnect on EOF
            '-reconnect_streamed', '1',           # Reconnect for streaming protocols
            '-reconnect_delay_max', '30',         # Max 30s retry delay
            '-fflags', '+genpts+discardcorrupt',  # Generate timestamps, discard corrupt
            '-rtbufsize', '15M',                  # Input buffer size
            '-analyzeduration', '10M',            # Analyze duration for format detection
            '-probesize', '10M',                  # Probe size for format detection
            '-i', source_url,
            # Output options
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-ac', '2',                           # Downmix to stereo for compatibility
            '-max_muxing_queue_size', '9999',     # Prevent muxing queue overflow
            '-f', 'flv',
            target_url
        ]

        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            # Give it a moment to fail if it's going to
            time.sleep(1)
            if process.poll() is not None:
                console.print("[red]✗[/red] Restream failed to start")
                self.wait_for_escape()
                return

            # Save metadata
            self.save_restream_meta(stream_key, channel_name, process.pid, source_url)

            # Show success
            console.print(f"[green]✓[/green] Restreaming: {channel_name}")
            console.print()
            console.print(f"[bright_yellow]HLS:[/bright_yellow] http://localhost:8080/hls/{stream_key}.m3u8")
            console.print(f"[bright_yellow]RTMP:[/bright_yellow] rtmp://localhost:1935/live/{stream_key}")
            time.sleep(2)

        except Exception as e:
            console.print(f"[red]✗[/red] Failed to start restream: {e}")
            self.wait_for_escape()

    def _start_restream(self, item, stream_key, transcode=False):
        """Start restreaming with FFmpeg"""
        console.clear()
        console.print(Panel.fit(f"Starting Restream: {item['name']}", style="dim white"))
        
        source_url = item.get('stream_url')
        if not source_url:
            console.print("[red]✗[/red] No stream URL available")
            self.wait_for_escape()
            return
        
        target_url = f"rtmp://localhost:1935/live/{stream_key}"
        
        if transcode:
            # Auto-detect platform and use appropriate encoder
            if self.is_raspberry_pi():
                # Raspberry Pi: Use hardware acceleration
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-hwaccel', 'v4l2m2m',                   # Hardware decoding
                    '-i', source_url,
                    '-c:v', 'h264_v4l2m2m',                  # Hardware H264 encoding for Pi
                    '-b:v', '1M',
                    '-maxrate', '1M',
                    '-bufsize', '2M',
                    '-vf', 'scale=854:480',
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-f', 'flv',
                    target_url
                ]
                console.print("Mode: Transcoding with Hardware Acceleration (Raspberry Pi)")
            else:
                # Other platforms: Use software encoding
                ffmpeg_cmd = [
                    'ffmpeg',
                    '-i', source_url,
                    '-c:v', 'libx264',
                    '-preset', 'superfast',
                    '-tune', 'zerolatency',
                    '-b:v', '1M',
                    '-maxrate', '1M',
                    '-bufsize', '2M',
                    '-vf', 'scale=854:480',
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-f', 'flv',
                    target_url
                ]
                console.print("Mode: Transcoding (Software Encoding)")
        else:
            # Copy without transcoding for best quality
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', source_url,
                '-c', 'copy',
                '-f', 'flv',
                target_url
            ]
            console.print("Mode: Copy (Best Quality)")
        
        console.print(f"Source: {source_url}")
        console.print(f"Target: {target_url}")
        console.print(f"View at: http://localhost:8080/hls/{stream_key}.m3u8")
        console.print()
        console.print("[yellow]Starting restream... This will run in the background.[/yellow]")
        console.print()
        
        try:
            # Start FFmpeg process in background
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )
            
            console.print(f"[green]✓[/green] Restream started with PID: {process.pid}")
            console.print()
            console.print("URLs for sharing:")
            console.print(f"• HLS Stream: http://localhost:8080/hls/{stream_key}.m3u8")
            console.print(f"• RTMP Stream: rtmp://localhost:1935/live/{stream_key}")
            console.print()
            console.print("The stream should be available in a few seconds.")
            console.print("Check 'Container Status & URLs' for monitoring.")

            # Save restream metadata
            source_url = item.get('stream_url') or item.get('url', '')
            self.save_restream_meta(stream_key, item.get('name', 'Unknown'), process.pid, source_url)
                
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to start restream: {e}")
        
        self.wait_for_escape()
    
    def _show_stream_urls(self, stream_key):
        """Show stream URLs for sharing"""
        console.clear()
        console.print(Panel.fit("Stream URLs", style="dim white"))
        
        console.print(f"[bright_yellow]Stream Key:[/bright_yellow] {stream_key}")
        console.print()
        console.print("[bright_yellow]📺 Viewing URLs:[/bright_yellow]")
        console.print(f"• HLS (Universal): http://localhost:8080/hls/{stream_key}.m3u8")
        console.print(f"• RTMP: rtmp://localhost:1935/live/{stream_key}")
        console.print()
        console.print("[bright_yellow]🎬 Player Instructions:[/bright_yellow]")
        console.print("• VLC: Open Network Stream → Paste HLS URL")
        console.print("• Browser: Use HLS.js player or native support")
        console.print("• OBS: Add Media Source → Paste HLS URL")
        console.print("• FFplay: ffplay 'http://localhost:8080/hls/{stream_key}.m3u8'")
        
        self.wait_for_escape()
    
    def _stop_restream(self):
        """Stop active restream processes"""
        console.clear()
        console.print(Panel.fit("Stop Restream", style="dim white"))

        # Check new metadata file first
        active = self.get_active_restream()

        if active:
            try:
                os.kill(active['pid'], signal.SIGTERM)
                console.print(f"[green]✓[/green] Stopped restream: {active['channel_name']}")
            except ProcessLookupError:
                console.print(f"[yellow]Restream already stopped: {active['channel_name']}[/yellow]")
            self.clear_restream_meta()
            self.wait_for_escape()
            return

        # Fallback: check old PID files for backwards compatibility
        pid_files = glob.glob(os.path.join(self.data_dir, ".restream_*.pid"))

        if not pid_files:
            console.print("No active restreams found")
            self.wait_for_escape()
            return

        stopped_count = 0
        for pid_file in pid_files:
            try:
                with open(pid_file, "r") as f:
                    pid = int(f.read().strip())

                # Try to terminate the process
                os.kill(pid, signal.SIGTERM)
                os.remove(pid_file)
                stopped_count += 1
                console.print(f"[green]✓[/green] Stopped restream process {pid}")

            except (OSError, ValueError, ProcessLookupError):
                # Process already dead or invalid PID
                try:
                    os.remove(pid_file)
                except:
                    pass

        if stopped_count > 0:
            console.print(f"[green]✓[/green] Stopped {stopped_count} restream(s)")
        else:
            console.print("[yellow]No active restreams to stop[/yellow]")

        self.wait_for_escape()
    
    def show_channel_details(self, channel):
        """Legacy method - redirect to new live stream info"""
        self.show_live_stream_info(channel)
    
    def preview_channel(self, channel_option):
        """Preview function for channel selection"""
        return f"Preview: {channel_option[:60]}..."
    
    def show_vod_categories(self):
        """Show VOD categories"""
        if not self.check_database():
            return
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        sql = """
            SELECT vc.category_name, COUNT(*) as count
            FROM vod_categories vc
            JOIN vod_streams vs ON vc.category_id = vs.category_id
            GROUP BY vc.category_name, vc.category_id
            ORDER BY count DESC, vc.category_name
            LIMIT 30
        """
        
        results = cursor.execute(sql).fetchall()
        conn.close()
        
        if not results:
            console.print("No VOD categories found")
            self.wait_for_escape()
            return
        
        console.clear()
        console.print(Panel.fit("VOD Categories", style="dim white"))
        
        options = [f"{row[0]} ({row[1]} movies/shows)" for row in results]
        options.append("Back")
        
        terminal_menu = TerminalMenu(
            options,
            title="",
            menu_cursor="> "
        )
        
        choice = terminal_menu.show()
        
        if choice is not None and choice < len(results):
            category_name = results[choice][0]
            self.show_vod_by_category(category_name)
    
    def show_vod_by_category(self, category_name):
        """Show VOD content in a specific category"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        sql = """
            SELECT vs.name, vs.stream_id, vs.stream_url, vs.year, vs.rating, vs.genre
            FROM vod_streams vs
            JOIN vod_categories vc ON vs.category_id = vc.category_id
            WHERE vc.category_name = ?
            ORDER BY CAST(vs.rating AS REAL) DESC, vs.name
            LIMIT 100
        """
        
        results = cursor.execute(sql, (category_name,)).fetchall()
        conn.close()
        
        if not results:
            console.print(f"No content found in category: {category_name}")
            self.wait_for_escape()
            return
        
        # Convert to dict format for show_vod_results
        vod_list = []
        for row in results:
            vod_dict = {
                'name': row[0],
                'stream_id': row[1], 
                'stream_url': row[2],
                'year': row[3],
                'rating': row[4],
                'genre': row[5]
            }
            vod_list.append(vod_dict)
        
        self.show_vod_results(vod_list, f"Category: {category_name}")
    
    def get_smart_recommendations(self, languages=['EN', 'FR'], min_rating=7.0, include_netflix=True, year_after=None, limit=50, sort_by_rating=True):
        """Get smart VOD recommendations based on language, rating, and other filters"""
        if not self.check_database():
            return []
            
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Build dynamic WHERE clause
        where_conditions = []
        params = []
        
        # Language filtering
        if languages:
            language_conditions = []
            for lang in languages:
                if lang.upper() == 'NETFLIX':
                    language_conditions.append("(vc.category_name LIKE '%NETFLIX%' OR vc.category_name LIKE 'NF -%')")
                else:
                    language_conditions.append("vc.category_name LIKE ?")
                    params.append(f"{lang.upper()} -%")
            
            if include_netflix and 'NETFLIX' not in [lang.upper() for lang in languages]:
                language_conditions.append("(vc.category_name LIKE '%NETFLIX%' OR vc.category_name LIKE 'NF -%')")
                
            where_conditions.append(f"({' OR '.join(language_conditions)})")
        
        # Rating filter
        if min_rating:
            where_conditions.append("vs.rating IS NOT NULL AND vs.rating <> '' AND CAST(vs.rating AS REAL) >= ?")
            params.append(min_rating)
        
        # Year filter
        if year_after:
            where_conditions.append("vs.year IS NOT NULL AND vs.year <> '' AND CAST(vs.year AS INTEGER) > ?")
            params.append(year_after)
        
        # Build final SQL query
        where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
        
        if sort_by_rating:
            order_clause = "ORDER BY CAST(vs.rating AS REAL) DESC, CAST(vs.year AS INTEGER) DESC"
        else:
            order_clause = "ORDER BY CAST(vs.year AS INTEGER) DESC, CAST(vs.rating AS REAL) DESC"
        
        sql = f"""
            SELECT vs.name, vs.stream_id, vs.stream_url, vs.year, vs.rating, vs.genre, vc.category_name
            FROM vod_streams vs
            JOIN vod_categories vc ON vs.category_id = vc.category_id
            WHERE {where_clause}
            {order_clause}
            LIMIT ?
        """
        
        params.append(limit)
        
        try:
            results = cursor.execute(sql, params).fetchall()
            conn.close()
            
            # Convert to dict format for show_vod_results
            vod_list = []
            for row in results:
                vod_dict = {
                    'name': row[0],
                    'stream_id': row[1], 
                    'stream_url': row[2],
                    'year': row[3],
                    'rating': row[4],
                    'genre': row[5],
                    'category_name': row[6]
                }
                vod_list.append(vod_dict)
            
            return vod_list
            
        except Exception as e:
            console.print(f"[red]Error getting smart recommendations: {e}[/red]")
            conn.close()
            return []
    
    def streaming_infrastructure_menu(self):
        """Streaming Infrastructure menu - manage Docker services"""
        while True:
            console.clear()
            self.display_header(show_status=False)
            console.print(Panel.fit("Streaming Infrastructure\n[dim white]Dashboard: http://localhost:8080[/dim white]", style="bright_cyan"))
            
            # Check installation status
            docker_installed = self.is_docker_installed()
            lazydocker_installed = self.is_lazydocker_installed()
            compose_exists = os.path.exists('docker-compose.yml')

            # Build simplified menu options - Lazydocker handles most management
            options = []

            # Primary option: Launch Lazydocker (if available)
            if docker_installed and lazydocker_installed:
                options.append("Launch Lazydocker")

            # Container status with URLs (unique value not in Lazydocker)
            if docker_installed:
                running_count = self.get_running_container_count()
                total_count = 3  # NGINX-RTMP, Jellyfin, Samba
                options.append(f"Container Status & URLs [{running_count}/{total_count} Running]")

                # Start/Stop container options
                if compose_exists:
                    if running_count < total_count:
                        options.append("Start All Containers")
                    if running_count > 0:
                        options.append("Stop All Containers")

            # Docker-compose configuration
            if docker_installed:
                if compose_exists:
                    options.append("Review/Edit docker-compose.yml")
                else:
                    options.append("Create docker-compose.yml")

            # Installation options (only show if not installed)
            if not docker_installed:
                options.append("Install Docker [Required]")
            if not lazydocker_installed:
                options.append("Install Lazydocker")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC pressed
                break
                
            selected_option = options[choice]
            
            # Handle selections based on option text
            if "Launch Lazydocker" in selected_option:
                self.launch_lazydocker()
            elif "Container Status & URLs" in selected_option:
                self.show_container_status_and_urls()
            elif "Review/Edit docker-compose.yml" in selected_option or "Create docker-compose.yml" in selected_option:
                self.edit_docker_compose()
            elif "Start All Containers" in selected_option:
                self.start_all_containers()
            elif "Stop All Containers" in selected_option:
                self.stop_all_containers()
            elif "Install Docker" in selected_option:
                self.install_docker()
            elif "Install Lazydocker" in selected_option:
                self.install_lazydocker()
    
    def container_management_menu(self):
        """Legacy redirect to new menu"""
        self.streaming_infrastructure_menu()
    
    def show_container_status_and_urls(self):
        """Show combined container status and URLs for both NGINX and Jellyfin"""
        console.clear()
        self.display_header(show_status=False)
        console.print(Panel.fit("Container Status & URLs", style="dim white"))
        
        # Check Docker status first
        docker_status = self.check_docker_status()
        console.print(f"Docker: {docker_status}")
        
        if "[green]" not in docker_status:
            console.print("\n[red]Docker is not running. Please install/start Docker first.[/red]")
            self.wait_for_escape()
            return
        
        console.print()
        
        # NGINX-RTMP Status
        nginx_status = self.check_container_status()
        console.print(f"[bright_yellow]NGINX-RTMP Container:[/bright_yellow]")
        console.print(f"  Status: {nginx_status}")
        
        if "[green]" in nginx_status:
            console.print("\n  [dim white]📡 RTMP Input:[/dim white]")
            console.print("    • rtmp://localhost:1935/live/[stream_key]")
            console.print("\n  [dim white]📺 HLS Output:[/dim white]")
            console.print("    • http://localhost:8080/hls/[stream_key].m3u8")
            console.print("\n  [dim white]🌐 Web Interfaces:[/dim white]")
            console.print("    • Player: http://localhost:8080")
            console.print("    • Stats: http://localhost:8080/stat")
            console.print("    • Admin: http://localhost:8081")
        
        console.print()
        
        # Jellyfin Status
        jellyfin_status = self.check_jellyfin_status()
        console.print(f"[bright_yellow]Jellyfin Media Server:[/bright_yellow]")
        console.print(f"  Status: {jellyfin_status}")
        
        if "[green]" in jellyfin_status:
            console.print("\n  [dim white]🌐 Web Interface:[/dim white]")
            console.print("    • http://localhost:8096")
            console.print("\n  [dim white]📱 Mobile Apps:[/dim white]")
            console.print("    • iOS: Jellyfin Mobile (App Store)")
            console.print("    • Android: Jellyfin (Google Play)")
            console.print("\n  [dim white]📺 TV Apps:[/dim white]")
            console.print("    • Roku, Fire TV, Android TV, Apple TV")
        
        console.print()
        
        # Samba Status
        samba_status = self.check_samba_status()
        console.print(f"[bright_yellow]Samba Network Share:[/bright_yellow]")
        console.print(f"  Status: {samba_status}")
        
        if "[green]" in samba_status:
            console.print("\n  [dim white]📁 Network Shares:[/dim white]")
            try:
                import socket
                hostname = socket.gethostname()
                server_ip = socket.gethostbyname(hostname)
                console.print(f"    • \\\\{server_ip}\\recordings")
                console.print(f"    • \\\\{server_ip}\\media")
                console.print(f"    • \\\\{server_ip}\\downloads")
            except:
                console.print("    • \\\\YOUR_SERVER_IP\\recordings")
                console.print("    • \\\\YOUR_SERVER_IP\\media")
                console.print("    • \\\\YOUR_SERVER_IP\\downloads")
            console.print("\n  [dim white]📱 Compatible TV Apps:[/dim white]")
            console.print("    • X-plore File Manager, Total Commander")
            console.print("    • Most built-in TV file managers")
        
        console.print()
        console.print("[dim white]Tip: Use 'Launch Lazydocker' for detailed container management[/dim white]")
        
        self.wait_for_escape()
    
    def build_and_start_all_containers(self):
        """Build and start all containers with docker-compose"""
        console.clear()
        console.print(Panel.fit("Build & Start All Containers", style="dim white"))
        
        # Check if Docker is available
        docker_status = self.check_docker_status()
        if "[green]" not in docker_status:
            console.print("[red]✗[/red] Docker is not available")
            console.print("Please install Docker first using the 'Install Docker' option")
            self.wait_for_escape()
            return
        
        console.print("[bright_yellow]This will build and start:[/bright_yellow]")
        console.print("• NGINX-RTMP Restreaming Server")
        console.print("• Jellyfin Media Server")
        console.print("• Samba Network Share")
        console.print()
        
        try:
            key = input()
            if key == '\x1b':  # ESC
                return
        except:
            pass
        
        console.print("\n[bright_yellow]Building and starting containers...[/bright_yellow]")
        console.print("This may take a few minutes on first run...")
        
        try:
            # Create necessary directories
            os.makedirs("jellyfin/config", exist_ok=True)
            os.makedirs("jellyfin/cache", exist_ok=True)
            os.makedirs("media", exist_ok=True)
            
            # Build and start with docker-compose
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                transient=True
            ) as progress:
                task = progress.add_task("Building containers...", total=None)
                
                result = subprocess.run(['docker-compose', 'build', '--no-cache'], 
                                      capture_output=True, check=True, timeout=600)
                
                progress.update(task, description="Starting containers...")
                
                result = subprocess.run(['docker-compose', 'up', '-d'], 
                                      capture_output=True, check=True, timeout=300)
            
            console.print("\n[green]✓[/green] All containers built and started successfully!")
            console.print("\n[bright_yellow]Access URLs:[/bright_yellow]")
            console.print("• NGINX-RTMP Player: http://localhost:8080")
            console.print("• Jellyfin: http://localhost:8096")
            console.print("\n[dim white]Tip: Use 'Launch Lazydocker' to monitor containers[/dim white]")
            
        except subprocess.CalledProcessError as e:
            console.print(f"\n[red]✗[/red] Error building/starting containers")
            if e.stderr:
                error_msg = e.stderr.decode()
                if "docker-compose: command not found" in error_msg:
                    console.print("Docker Compose is not installed. Please install Docker first.")
                else:
                    console.print(f"Error: {error_msg[:500]}...")
        except Exception as e:
            console.print(f"\n[red]✗[/red] Unexpected error: {e}")
        
        self.wait_for_escape()
    
    # The following container menu methods are deprecated - replaced by Launch Lazydocker
    # def nginx_container_menu(self):
    #     """NGINX Container management menu - DEPRECATED"""
    #     pass
    
    # def jellyfin_container_menu(self):  
    #     """Jellyfin Container management menu - DEPRECATED"""
    #     pass
    
    def check_docker_status(self):
        """Check if Docker is available"""
        try:
            result = subprocess.run(['docker', '--version'], capture_output=True, check=True, timeout=5)
            return "[green]✓ Available[/green]"
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return "[red]✗ Not available[/red]"
    
    def is_docker_installed(self):
        """Check if Docker is installed (returns boolean)"""
        try:
            subprocess.run(['docker', '--version'], capture_output=True, check=True, timeout=5)
            return True
        except:
            return False
    
    def is_lazydocker_installed(self):
        """Check if lazydocker is installed (returns boolean)"""
        try:
            subprocess.run(['which', 'lazydocker'], capture_output=True, check=True, timeout=5)
            return True
        except:
            return False
    
    def get_running_container_count(self):
        """Get count of running containers (NGINX, Jellyfin, Samba)"""
        count = 0
        containers = ['iptv-nginx-rtmp', 'iptv-jellyfin', 'iptv-samba']
        
        for container in containers:
            try:
                result = subprocess.run(['docker', 'ps', '--filter', f'name={container}', '--format', '{{.Status}}'],
                                      capture_output=True, check=True, timeout=5)
                if 'Up' in result.stdout.decode():
                    count += 1
            except:
                pass
        
        return count
    
    def edit_docker_compose(self):
        """Edit or create docker-compose.yml file"""
        console.clear()
        self.display_header(show_status=False)
        console.print(Panel.fit("Edit docker-compose.yml", style="dim white"))
        
        compose_file = "docker-compose.yml"
        
        # Check if file exists
        if not os.path.exists(compose_file):
            console.print("[yellow]⚠[/yellow] docker-compose.yml not found")
            console.print("Would you like to create a default one? (y/n): ", end="")
            
            try:
                response = input().strip().lower()
                if response == 'y':
                    self.create_default_docker_compose()
                else:
                    console.print("Cancelled")
                    self.wait_for_escape()
                    return
            except KeyboardInterrupt:
                return
        
        # Open in nano editor
        console.print(f"Opening {compose_file} in nano editor...")
        console.print("[dim]Press Ctrl+X to exit, Y to save changes[/dim]")
        console.print()
        
        try:
            # Check for preferred editor
            editor = os.environ.get('EDITOR', 'nano')
            subprocess.run([editor, compose_file])
            
            console.print()
            console.print("[green]✓[/green] Editor closed")
            
            # Validate docker-compose file
            console.print("Validating docker-compose.yml...")
            result = subprocess.run(['docker-compose', 'config', '-q'], 
                                  capture_output=True, timeout=10)
            
            if result.returncode == 0:
                console.print("[green]Configuration is valid![/green]")
                console.print()
                console.print("[dim]Use Lazydocker to start/restart services[/dim]")
            else:
                console.print("[red]✗[/red] docker-compose.yml has errors:")
                console.print(result.stderr.decode())
                
        except FileNotFoundError:
            console.print(f"[red]✗[/red] Editor '{editor}' not found. Install nano or set EDITOR environment variable.")
        except Exception as e:
            console.print(f"[red]✗[/red] Error editing file: {e}")
        
        self.wait_for_escape()

    def validate_docker_compose(self):
        """Validate docker-compose.yml configuration"""
        console.clear()
        self.display_header(show_status=False)
        console.print(Panel.fit("Validate docker-compose.yml", style="dim white"))

        if not os.path.exists('docker-compose.yml'):
            console.print("[red]docker-compose.yml not found[/red]")
            self.wait_for_escape()
            return

        console.print("Validating configuration...")
        console.print()

        try:
            result = subprocess.run(
                ['docker-compose', 'config'],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                console.print("[green]Configuration is valid![/green]")
                console.print()
                console.print("[dim]Resolved configuration:[/dim]")
                # Truncate output if too long
                output = result.stdout
                if len(output) > 3000:
                    output = output[:3000] + "\n... (truncated)"
                console.print(output)
            else:
                console.print("[red]Configuration has errors:[/red]")
                console.print(result.stderr)
        except FileNotFoundError:
            console.print("[red]docker-compose not found. Please install Docker Compose.[/red]")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Validation timed out[/yellow]")
        except Exception as e:
            console.print(f"[red]Validation failed: {e}[/red]")

        self.wait_for_escape()

    def start_all_containers(self):
        """Start all Docker containers using docker-compose"""
        console.clear()
        self.display_header(show_status=False)
        console.print(Panel.fit("Starting Containers", style="bright_cyan"))
        console.print()

        try:
            console.print("[dim]Running docker-compose up -d --build...[/dim]")
            console.print()
            result = subprocess.run(
                ['docker-compose', 'up', '-d', '--build'],
                capture_output=False,
                timeout=300
            )
            console.print()
            if result.returncode == 0:
                console.print("[green]All containers started successfully![/green]")
            else:
                console.print("[red]Some containers may have failed to start[/red]")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Operation timed out (5 min limit)[/yellow]")
        except FileNotFoundError:
            console.print("[red]docker-compose not found. Please install Docker Compose.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        self.wait_for_escape()

    def stop_all_containers(self):
        """Stop all Docker containers using docker-compose"""
        console.clear()
        self.display_header(show_status=False)
        console.print(Panel.fit("Stopping Containers", style="bright_yellow"))
        console.print()

        try:
            console.print("[dim]Running docker-compose down...[/dim]")
            console.print()
            result = subprocess.run(
                ['docker-compose', 'down'],
                capture_output=False,
                timeout=60
            )
            console.print()
            if result.returncode == 0:
                console.print("[green]All containers stopped successfully![/green]")
            else:
                console.print("[yellow]Some containers may not have stopped cleanly[/yellow]")
        except subprocess.TimeoutExpired:
            console.print("[yellow]Operation timed out (60 sec limit)[/yellow]")
        except FileNotFoundError:
            console.print("[red]docker-compose not found. Please install Docker Compose.[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        self.wait_for_escape()

    def create_default_docker_compose(self):
        """Create a default docker-compose.yml file"""
        default_compose = """version: '3.8'

services:
  nginx-rtmp:
    container_name: iptv-nginx-rtmp
    build: ./nginx
    ports:
      - "1935:1935"
      - "8080:8080"
      - "8081:8081"
    volumes:
      - ./nginx/recordings:/recordings
      - ./nginx/hls:/tmp/hls
    restart: unless-stopped

  jellyfin:
    container_name: iptv-jellyfin
    image: jellyfin/jellyfin:latest
    ports:
      - "8096:8096"
    volumes:
      - ./jellyfin/config:/config
      - ./jellyfin/cache:/cache
      - ./media:/media/library
      - ./nginx/recordings:/media/recordings
    restart: unless-stopped
    environment:
      - JELLYFIN_PublishedServerUrl=http://localhost:8096

  samba:
    container_name: iptv-samba
    image: dperson/samba:latest
    ports:
      - "137:137/udp"
      - "138:138/udp"
      - "139:139"
      - "445:445"
    volumes:
      - ./nginx/recordings:/recordings
      - ./media:/media
      - ./data:/downloads
    environment:
      - USERID=1000
      - GROUPID=1000
    command: >
      -s "recordings;/recordings;yes;no;yes;all"
      -s "media;/media;yes;no;yes;all"
      -s "downloads;/downloads;yes;no;yes;all"
      -u "iptv;iptv123"
      -p
    restart: unless-stopped

networks:
  default:
    name: iptv-network
"""
        
        try:
            with open('docker-compose.yml', 'w') as f:
                f.write(default_compose)
            console.print("[green]✓[/green] Created default docker-compose.yml")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to create docker-compose.yml: {e}")
    
    def check_container_status(self):
        """Check NGINX container status"""
        try:
            result = subprocess.run(['docker', 'ps', '--filter', 'name=iptv-nginx-rtmp', '--format', 'table {{.Status}}'], 
                                  capture_output=True, check=True, timeout=5)
            output = result.stdout.decode().strip()
            if 'Up' in output:
                return "[green]✓ Running[/green]"
            else:
                # Check if container exists but stopped
                result = subprocess.run(['docker', 'ps', '-a', '--filter', 'name=iptv-nginx-rtmp', '--format', 'table {{.Status}}'], 
                                      capture_output=True, check=True, timeout=5)
                output = result.stdout.decode().strip()
                if output and output != "STATUS":
                    return "[yellow]○ Stopped[/yellow]"
                else:
                    return "[dim white]○ Not created[/dim white]"
        except:
            return "[dim white]○ Unknown[/dim white]"
    
    def build_nginx_container(self):
        """Build and start NGINX container"""
        console.clear()
        console.print(Panel.fit("Building NGINX-RTMP Container", style="dim white"))
        
        # Check if Docker is available
        if "[red]" in self.check_docker_status():
            console.print("[red]✗[/red] Docker is not available. Please install Docker and Docker Compose.")
            console.print("Installation: https://docs.docker.com/get-docker/")
            self.wait_for_escape()
            return
        
        try:
            console.print("Building and starting containers...")
            
            # Use docker-compose to build and start
            process = subprocess.Popen(
                ['docker-compose', 'up', '-d', '--build'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True
            )
            
            # Show output in real-time
            for line in process.stdout:
                console.print(f"[dim white]{line.strip()}[/dim white]")
            
            process.wait()
            
            if process.returncode == 0:
                console.print("\n[green]✓[/green] Container started successfully!")
                console.print("\nServer URLs:")
                console.print("• Web Interface: http://localhost:8080")
                console.print("• RTMP Input: rtmp://localhost:1935/live/[stream_key]")
                console.print("• HLS Output: http://localhost:8080/hls/[stream_key].m3u8")
                console.print("• Statistics: http://localhost:8080/stat")
            else:
                console.print(f"[red]✗[/red] Container build failed with exit code: {process.returncode}")
                
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found. Please install Docker Compose.")
        except Exception as e:
            console.print(f"[red]✗[/red] Error building container: {e}")
        
        self.wait_for_escape()
    
    def stop_nginx_container(self):
        """Stop NGINX container"""
        console.clear()
        console.print(Panel.fit("Stopping NGINX Container", style="dim white"))
        
        try:
            result = subprocess.run(['docker-compose', 'down'], 
                                  capture_output=True, check=True, timeout=30)
            console.print("[green]✓[/green] Container stopped successfully")
            console.print(result.stdout.decode())
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Error stopping container: {e}")
            console.print(e.stderr.decode())
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found")
        except Exception as e:
            console.print(f"[red]✗[/red] Error: {e}")
        
        self.wait_for_escape()
    
    def show_container_logs(self):
        """Show container logs"""
        console.clear()
        console.print(Panel.fit("Container Logs", style="dim white"))
        
        try:
            result = subprocess.run(['docker', 'logs', 'iptv-nginx-rtmp', '--tail', '50'], 
                                  capture_output=True, check=True, timeout=10)
            console.print(result.stdout.decode())
            if result.stderr.decode():
                console.print(f"[yellow]Stderr:[/yellow] {result.stderr.decode()}")
        except subprocess.CalledProcessError:
            console.print("[yellow]Container not found or not running[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def show_container_status(self):
        """Show detailed container status and URLs"""
        console.clear()
        console.print(Panel.fit("Container Status & Information", style="dim white"))
        
        # Container status
        status = self.check_container_status()
        console.print(f"Status: {status}")
        
        if "[green]" not in status:
            console.print("\nContainer is not running. Use 'Build & Start NGINX Container' first.")
            self.wait_for_escape()
            return
        
        console.print("\n[bright_yellow]📡 RTMP Input Endpoints:[/bright_yellow]")
        console.print("• Main: rtmp://localhost:1935/live/[stream_key]")
        console.print("• Example: rtmp://localhost:1935/live/cnn_news")
        
        console.print("\n[bright_yellow]📺 HLS Output URLs:[/bright_yellow]")
        console.print("• Base: http://localhost:8080/hls/[stream_key].m3u8")
        console.print("• Example: http://localhost:8080/hls/cnn_news.m3u8")
        
        console.print("\n[bright_yellow]🌐 Web Interfaces:[/bright_yellow]")
        console.print("• Main: http://localhost:8080")
        console.print("• Statistics: http://localhost:8080/stat")
        console.print("• Admin: http://localhost:8081")
        
        console.print("\n[bright_yellow]📊 Quality Variants:[/bright_yellow]")
        console.print("• Source: Original quality")
        console.print("• Mid: 854x480 @ 768k")
        console.print("• Low: 480x270 @ 256k")
        
        self.wait_for_escape()
    
    def test_restream_setup(self):
        """Test the restreaming setup"""
        console.clear()
        console.print(Panel.fit("Test Restream Setup", style="dim white"))
        
        # Check if FFmpeg is available
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True, timeout=5)
            console.print("[green]✓[/green] FFmpeg is available")
        except:
            console.print("[red]✗[/red] FFmpeg not found. Install with:")
            console.print("Ubuntu/Debian: sudo apt install ffmpeg")
            console.print("macOS: brew install ffmpeg")
            self.wait_for_escape()
            return
        
        # Check container status
        if "[green]" not in self.check_container_status():
            console.print("[red]✗[/red] NGINX container is not running")
            console.print("Start the container first using 'Build & Start NGINX Container'")
            self.wait_for_escape()
            return
        
        console.print("\n[bright_yellow]Testing with sample stream...[/bright_yellow]")
        console.print("This will test the restream setup with a color bar test pattern.")
        console.print("Press Enter to start test, or Escape to cancel...")
        
        try:
            key = input()
            if key == '\x1b':  # ESC
                return
        except:
            pass
        
        try:
            # Create a test stream with FFmpeg
            console.print("Starting test stream...")
            test_cmd = [
                'ffmpeg',
                '-f', 'lavfi',
                '-i', 'testsrc2=size=640x480:rate=1',
                '-f', 'lavfi', 
                '-i', 'sine=frequency=1000',
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency', 
                '-c:a', 'aac',
                '-f', 'flv',
                '-t', '10',  # 10 second test
                'rtmp://localhost:1935/live/test_stream'
            ]
            
            process = subprocess.Popen(test_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            console.print("Test stream running for 10 seconds...")
            console.print("You can view it at: http://localhost:8080/hls/test_stream.m3u8")
            
            stdout, stderr = process.communicate(timeout=15)
            
            if process.returncode == 0:
                console.print("[green]✓[/green] Test stream completed successfully!")
            else:
                console.print(f"[yellow]⚠[/yellow] Test completed with warnings")
                if stderr:
                    console.print(f"Details: {stderr.decode()[:200]}...")
                    
        except subprocess.TimeoutExpired:
            process.kill()
            console.print("[yellow]⚠[/yellow] Test stream timed out (this is normal)")
        except Exception as e:
            console.print(f"[red]✗[/red] Test failed: {e}")
        
        self.wait_for_escape()
    
    def download_full(self):
        """Download full database"""
        console.print("\nStarting full database download...")
        success = self._download_and_create_db([
            "account_info", "live_categories", "live_streams",
            "vod_categories", "vod_streams", "series_categories", "series_streams"
        ])
        
        if success:
            console.print("Database download completed successfully!")
        else:
            console.print("Database download failed!")
        
        self.wait_for_escape()
    
    def download_live_only(self):
        """Download live streams only"""
        console.print("\nUpdating live streams...")
        success = self._download_and_create_db(["live_categories", "live_streams"])
        
        if success:
            console.print("Live streams updated successfully!")
        else:
            console.print("Live streams update failed!")
        
        self.wait_for_escape()
    
    def download_vod_only(self):
        """Download VOD content only"""
        console.print("\nDownloading VOD content...")
        success = self._download_and_create_db(["vod_categories", "vod_streams"])
        
        if success:
            console.print("VOD content downloaded successfully!")
        else:
            console.print("VOD content download failed!")
        
        self.wait_for_escape()
    
    def _download_and_create_db(self, components):
        """Download specified components and update database"""
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                
                main_task = progress.add_task("Downloading...", total=len(components) + 1)
                
                # Download each component
                for component in components:
                    task = progress.add_task(f"Downloading {component}", total=1)
                    
                    if component == "account_info":
                        success = self._download_account_info()
                    elif component == "live_categories":
                        success = self._download_live_categories()
                    elif component == "live_streams":
                        success = self._download_live_streams()
                    elif component == "vod_categories":
                        success = self._download_vod_categories()
                    elif component == "vod_streams":
                        success = self._download_vod_streams()
                    elif component == "series_categories":
                        success = self._download_series_categories()
                    elif component == "series_streams":
                        success = self._download_series_streams()
                    else:
                        success = False
                    
                    if not success:
                        return False
                    
                    progress.update(task, completed=1)
                    progress.advance(main_task)
                
                # Create/update database
                db_task = progress.add_task("Creating database", total=1)
                success = self._create_database()
                progress.update(db_task, completed=1)
                progress.advance(main_task)

            # EPG is now lazy-loaded on demand, not during update
            return success
                
        except Exception as e:
            console.print(f"Download error: {e}")
            return False
    
    def _download_account_info(self):
        """Download account info"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                with open(os.path.join(self.data_dir, "account_info.json"), "w") as f:
                    json.dump(data, f, indent=2)
                return True
            else:
                console.print(f"[red]✗[/red] HTTP Error: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            console.print(f"[red]✗[/red] Network error: {e}")
            return False
        except json.JSONDecodeError as e:
            console.print(f"[red]✗[/red] JSON decode error: {e}")
            return False
        except Exception as e:
            console.print(f"[red]✗[/red] Unexpected error: {e}")
            return False
    
    def _download_live_categories(self):
        """Download live categories"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_live_categories"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "live_categories.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False
    
    def _download_live_streams(self):
        """Download live streams"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_live_streams"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=120)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "live_streams.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False
    
    def _download_vod_categories(self):
        """Download VOD categories"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_vod_categories"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "vod_categories.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False
    
    def _download_vod_streams(self):
        """Download VOD streams"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_vod_streams"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=120)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "vod_streams.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False
    
    def _download_series_categories(self):
        """Download series categories"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_series_categories"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "series_categories.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False

    def _download_series_streams(self):
        """Download series streams (TV shows)"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_series"
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=120)
            if response.status_code == 200:
                with open(os.path.join(self.data_dir, "series_streams.json"), "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False

    def _download_epg(self):
        """Download EPG data for all live channels and store in database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get all stream_ids from live_streams
            cursor.execute("SELECT stream_id, name FROM live_streams")
            channels = cursor.fetchall()

            if not channels:
                conn.close()
                return True

            console.print(f"\n[cyan]Downloading EPG for {len(channels)} channels...[/cyan]")

            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            success_count = 0
            error_count = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                console=console
            ) as progress:
                task = progress.add_task("Downloading EPG", total=len(channels))

                for stream_id, name in channels:
                    try:
                        url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_short_epg&stream_id={stream_id}&limit=10"
                        response = requests.get(url, headers=headers, timeout=5)

                        if response.status_code == 200:
                            epg_data = response.json()
                            listings = epg_data.get('epg_listings', []) if isinstance(epg_data, dict) else []

                            for listing in listings:
                                try:
                                    start_time = int(listing.get('start_timestamp', 0))
                                    end_time = int(listing.get('stop_timestamp', 0))
                                    title = self._decode_base64_if_needed(listing.get('title', '')) or listing.get('title', '')
                                    description = self._decode_base64_if_needed(listing.get('description', '')) or listing.get('description', '')

                                    if start_time and end_time:
                                        cursor.execute('''
                                            INSERT OR REPLACE INTO epg (stream_id, start_time, end_time, title, description)
                                            VALUES (?, ?, ?, ?, ?)
                                        ''', (stream_id, start_time, end_time, title, description))
                                except:
                                    pass

                            if listings:
                                success_count += 1
                    except:
                        error_count += 1

                    progress.advance(task)

            conn.commit()
            conn.close()

            console.print(f"[green]EPG downloaded: {success_count} channels with data, {error_count} errors[/green]")
            return True

        except Exception as e:
            console.print(f"[red]EPG download error: {e}[/red]")
            return False

    def _create_database(self):
        """Create/update SQLite database"""
        try:
            # Remove old database for fresh creation
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create tables
            cursor.execute('''
                CREATE TABLE live_streams (
                    stream_id INTEGER PRIMARY KEY,
                    name TEXT,
                    category_id INTEGER,
                    stream_url TEXT,
                    category_name TEXT,
                    epg_channel_id TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE vod_streams (
                    stream_id INTEGER PRIMARY KEY,
                    name TEXT,
                    category_id INTEGER,
                    stream_url TEXT,
                    year TEXT,
                    rating REAL,
                    genre TEXT
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE vod_categories (
                    category_id INTEGER PRIMARY KEY,
                    category_name TEXT,
                    parent_id INTEGER
                )
            ''')
            
            cursor.execute('''
                CREATE TABLE account_info (
                    username TEXT,
                    status TEXT,
                    exp_date INTEGER,
                    max_connections TEXT
                )
            ''')

            cursor.execute('''
                CREATE TABLE epg (
                    stream_id INTEGER,
                    start_time INTEGER,
                    end_time INTEGER,
                    title TEXT,
                    description TEXT,
                    cached_at INTEGER,
                    PRIMARY KEY (stream_id, start_time)
                )
            ''')

            cursor.execute('''
                CREATE TABLE series_streams (
                    series_id INTEGER PRIMARY KEY,
                    name TEXT,
                    category_id INTEGER,
                    cover TEXT,
                    plot TEXT,
                    cast TEXT,
                    genre TEXT,
                    rating REAL,
                    category_name TEXT
                )
            ''')

            # Load data from JSON files
            self._load_data_from_json(cursor)
            
            # Create indexes
            cursor.execute("CREATE INDEX idx_live_name ON live_streams(name)")
            cursor.execute("CREATE INDEX idx_vod_name ON vod_streams(name)")
            cursor.execute("CREATE INDEX idx_vod_cat_name ON vod_categories(category_name)")
            cursor.execute("CREATE INDEX idx_epg_stream ON epg(stream_id)")
            cursor.execute("CREATE INDEX idx_epg_time ON epg(start_time, end_time)")
            cursor.execute("CREATE INDEX idx_series_name ON series_streams(name)")
            
            conn.commit()
            conn.close()
            
            return True
            
        except Exception as e:
            console.print(f"Database creation error: {e}")
            return False
    
    def _load_data_from_json(self, cursor):
        """Load data from JSON files into database"""
        # Load account info
        account_info_path = os.path.join(self.data_dir, "account_info.json")
        if os.path.exists(account_info_path):
            with open(account_info_path) as f:
                data = json.load(f)
                user_info = data.get('user_info', {})
                cursor.execute('''
                    INSERT INTO account_info VALUES (?, ?, ?, ?)
                ''', (user_info.get('username'), user_info.get('status'),
                     user_info.get('exp_date'), user_info.get('max_connections')))
        
        # Load VOD categories
        vod_categories_path = os.path.join(self.data_dir, "vod_categories.json")
        if os.path.exists(vod_categories_path):
            with open(vod_categories_path) as f:
                vod_cats = json.load(f)
                for cat in vod_cats:
                    cursor.execute('''
                        INSERT INTO vod_categories VALUES (?, ?, ?)
                    ''', (cat.get('category_id'), cat.get('category_name'), cat.get('parent_id')))
        
        # Load live categories map
        categories = {}
        live_categories_path = os.path.join(self.data_dir, "live_categories.json")
        if os.path.exists(live_categories_path):
            with open(live_categories_path) as f:
                cats = json.load(f)
                for cat in cats:
                    categories[cat.get('category_id')] = cat.get('category_name')
        
        # Load live streams
        live_streams_path = os.path.join(self.data_dir, "live_streams.json")
        if os.path.exists(live_streams_path):
            with open(live_streams_path) as f:
                streams = json.load(f)
                for stream in streams:
                    stream_url = f"{self.server}/live/{self.username}/{self.password}/{stream.get('stream_id')}.ts"
                    cat_name = categories.get(stream.get('category_id'), 'Unknown')
                    
                    cursor.execute('''
                        INSERT INTO live_streams VALUES (?, ?, ?, ?, ?, ?)
                    ''', (stream.get('stream_id'), stream.get('name'),
                         stream.get('category_id'), stream_url, cat_name,
                         stream.get('epg_channel_id', '')))
        
        # Load VOD streams
        vod_streams_path = os.path.join(self.data_dir, "vod_streams.json")
        if os.path.exists(vod_streams_path):
            with open(vod_streams_path) as f:
                streams = json.load(f)
                for stream in streams:
                    container_ext = stream.get('container_extension', 'mp4')
                    stream_url = f"{self.server}/movie/{self.username}/{self.password}/{stream.get('stream_id')}.{container_ext}"

                    cursor.execute('''
                        INSERT INTO vod_streams VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (stream.get('stream_id'), stream.get('name'),
                         stream.get('category_id'), stream_url,
                         stream.get('year'), stream.get('rating'), stream.get('genre')))

        # Load series categories map
        series_categories = {}
        series_categories_path = os.path.join(self.data_dir, "series_categories.json")
        if os.path.exists(series_categories_path):
            with open(series_categories_path) as f:
                cats = json.load(f)
                for cat in cats:
                    series_categories[cat.get('category_id')] = cat.get('category_name')

        # Load series streams (TV shows)
        series_streams_path = os.path.join(self.data_dir, "series_streams.json")
        if os.path.exists(series_streams_path):
            with open(series_streams_path) as f:
                series = json.load(f)
                for show in series:
                    cat_name = series_categories.get(str(show.get('category_id')), 'Unknown')

                    cursor.execute('''
                        INSERT INTO series_streams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (show.get('series_id'), show.get('name'),
                         show.get('category_id'), show.get('cover'),
                         show.get('plot'), show.get('cast'),
                         show.get('genre'), show.get('rating'), cat_name))

    def check_jellyfin_status(self):
        """Check Jellyfin container status"""
        try:
            result = subprocess.run(['docker', 'ps', '--filter', 'name=iptv-jellyfin', '--format', 'table {{.Status}}'], 
                                  capture_output=True, check=True, timeout=5)
            if result.stdout.strip():
                lines = result.stdout.decode().strip().split('\n')
                if len(lines) > 1:  # Skip header
                    status = lines[1].strip()
                    if "Up" in status:
                        return "[green]✓ Running[/green]"
                    else:
                        return f"[yellow]○ {status}[/yellow]"
                else:
                    return "[dim white]○ Not created[/dim white]"
        except:
            return "[dim white]○ Unknown[/dim white]"
    
    def check_samba_status(self):
        """Check Samba container status"""
        try:
            result = subprocess.run(['docker', 'ps', '--filter', 'name=iptv-samba', '--format', 'table {{.Status}}'], 
                                  capture_output=True, check=True, timeout=5)
            if result.stdout.strip():
                lines = result.stdout.decode().strip().split('\n')
                if len(lines) > 1:  # Skip header
                    status = lines[1].strip()
                    if "Up" in status:
                        return "[green]✓ Running[/green]"
                    else:
                        return f"[yellow]○ {status}[/yellow]"
                else:
                    return "[dim white]○ Not created[/dim white]"
        except:
            return "[dim white]○ Unknown[/dim white]"
    
    def build_jellyfin_container(self):
        """Build and start Jellyfin container"""
        console.clear()
        console.print(Panel.fit("Building Jellyfin Media Server Container", style="dim white"))
        
        # Check if Docker is available
        docker_status = self.check_docker_status()
        if "[green]" not in docker_status:
            console.print("[red]✗[/red] Docker is not available")
            self.wait_for_escape()
            return
        
        console.print("Building and starting Jellyfin container...")
        console.print("This may take a few minutes...")
        
        try:
            # Create necessary directories
            os.makedirs("jellyfin/config", exist_ok=True)
            os.makedirs("jellyfin/cache", exist_ok=True)
            os.makedirs("media", exist_ok=True)
            
            # Build and start only Jellyfin service
            result = subprocess.run(['docker-compose', 'up', '-d', 'jellyfin'], 
                                  capture_output=True, check=True, timeout=300)
            
            console.print("[green]✓[/green] Jellyfin container started successfully!")
            console.print("\nContainer Information:")
            console.print("• Web Interface: http://localhost:8096")
            console.print("• Setup will be required on first run")
            console.print("• Media path: /media/library (maps to ./media)")
            console.print("• Recordings path: /media/recordings (maps to ./nginx/recordings)")
            
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
            if result.stderr.decode():
                console.print(f"[dim]Stderr:[/dim] {result.stderr.decode()}")
                
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error building container: {e}[/red]")
            if e.stdout:
                console.print(f"Stdout: {e.stdout.decode()}")
            if e.stderr:
                console.print(f"Stderr: {e.stderr.decode()}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def stop_jellyfin_container(self):
        """Stop Jellyfin container"""
        console.clear()
        console.print(Panel.fit("Stopping Jellyfin Container", style="dim white"))
        
        try:
            result = subprocess.run(['docker-compose', 'stop', 'jellyfin'], 
                                  capture_output=True, check=True, timeout=30)
            console.print("[green]✓[/green] Jellyfin container stopped successfully!")
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error stopping container: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def show_jellyfin_logs(self):
        """Show Jellyfin container logs"""
        console.clear()
        console.print(Panel.fit("Jellyfin Container Logs", style="dim white"))
        
        try:
            result = subprocess.run(['docker', 'logs', 'iptv-jellyfin', '--tail', '50'], 
                                  capture_output=True, check=True, timeout=10)
            console.print(result.stdout.decode())
            if result.stderr.decode():
                console.print(f"[yellow]Stderr:[/yellow] {result.stderr.decode()}")
        except subprocess.CalledProcessError:
            console.print("[yellow]Container not found or not running[/yellow]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def show_jellyfin_status(self):
        """Show detailed Jellyfin container status and URLs"""
        console.clear()
        console.print(Panel.fit("Jellyfin Container Status & Information", style="dim white"))
        
        # Container status
        status = self.check_jellyfin_status()
        console.print(f"Status: {status}")
        
        if "[green]" not in status:
            console.print("\nContainer is not running. Use 'Build & Start Jellyfin Container' first.")
            self.wait_for_escape()
            return
        
        # Show URLs and information
        jellyfin_port = os.getenv('JELLYFIN_HTTP_PORT', '8096')
        jellyfin_https_port = os.getenv('JELLYFIN_HTTPS_PORT', '8920')
        
        console.print("\n[bright_yellow]Access Information:[/bright_yellow]")
        console.print(f"• Web Interface: http://localhost:{jellyfin_port}")
        console.print(f"• HTTPS Interface: https://localhost:{jellyfin_https_port}")
        console.print(f"• Network Access: http://YOUR_SERVER_IP:{jellyfin_port}")
        
        console.print("\n[bright_yellow]Media Paths:[/bright_yellow]")
        media_path = os.getenv('JELLYFIN_MEDIA_PATH', './media')
        console.print(f"• Media Library: {os.path.abspath(media_path)}")
        console.print(f"• NGINX Recordings: {os.path.abspath('./nginx/recordings')}")
        
        console.print("\n[bright_yellow]First Time Setup:[/bright_yellow]")
        console.print("1. Open web interface in browser")
        console.print("2. Create admin user account")
        console.print("3. Add media libraries:")
        console.print("   - Library: /media/library (your USB drive)")
        console.print("   - Recordings: /media/recordings (NGINX recordings)")
        
        self.wait_for_escape()
    
    def build_and_start_samba_container(self):
        """Build and start Samba network share container"""
        console.clear()
        console.print(Panel.fit("Building Samba Network Share Container", style="dim white"))
        
        # Check if Docker is available
        docker_status = self.check_docker_status()
        if "[green]" not in docker_status:
            console.print("[red]✗[/red] Docker is not available")
            self.wait_for_escape()
            return
        
        console.print("Building and starting Samba container...")
        console.print("This will create network shares accessible from your TV devices.\n")
        
        try:
            # Build and start using docker-compose
            result = subprocess.run(['docker-compose', 'up', '-d', '--build', 'samba'], 
                                  capture_output=True, check=True, timeout=120)
            
            console.print("[green]✓[/green] Samba container built and started successfully!")
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
            
            # Show connection info
            console.print("\n[bright_yellow]Network Share Information:[/bright_yellow]")
            console.print("• Share Name: \\\\YOUR_SERVER_IP\\recordings")
            console.print("• Share Name: \\\\YOUR_SERVER_IP\\media")
            console.print("• Share Name: \\\\YOUR_SERVER_IP\\downloads")
            console.print("\n[bright_yellow]TV Device Connection:[/bright_yellow]")
            console.print("1. Use file manager on your TV (X-plore, Total Commander)")
            console.print("2. Add network location > SMB/CIFS")
            console.print("3. Enter your server IP address")
            console.print("4. Select the share you want to use for recordings")
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error building container: {e}[/red]")
            console.print(f"[red]Error output: {e.stderr.decode() if e.stderr else 'No error output'}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def configure_samba_users(self):
        """Configure Samba users and shares"""
        console.clear()
        console.print(Panel.fit("Samba Configuration Information", style="dim white"))
        
        # Check if Samba container is running
        samba_status = self.check_samba_status()
        if "[green]" not in samba_status:
            console.print("[red]✗[/red] Samba container is not running")
            console.print("Please build and start the Samba container first.")
            self.wait_for_escape()
            return
        
        console.print("[bright_green]✓[/bright_green] Current configuration: Guest access (no password required)")
        console.print("This is perfect for TV devices - they can connect immediately.\n")
        
        console.print("[bright_yellow]Current Shares:[/bright_yellow]")
        console.print("• recordings → ./nginx/recordings (for TV recording)")
        console.print("• media → ./media (your media library)")  
        console.print("• downloads → ./downloads (downloaded content)\n")
        
        console.print("[bright_yellow]To Modify Configuration:[/bright_yellow]")
        console.print("All Samba settings are configured in: [cyan]docker-compose.yml[/cyan]")
        console.print("\n[dim white]To change folders or add authentication:[/dim white]")
        console.print("1. Edit the 'samba' service section in docker-compose.yml")
        console.print("2. Modify volumes to point to your desired folders")
        console.print("3. Add -u \"username;password\" to command for authentication")
        console.print("4. Rebuild container: docker-compose up -d --build samba")
        console.print("\n[dim white]Current setup is optimal for TV device compatibility.[/dim white]")
        
        self.wait_for_escape()
    
    def show_samba_container_status(self):
        """Show detailed Samba container status and connection info"""
        console.clear()
        console.print(Panel.fit("Samba Container Status", style="dim white"))
        
        # Show container status
        samba_status = self.check_samba_status()
        console.print(f"Samba Container: {samba_status}")
        
        if "[green]" not in samba_status:
            console.print("\n[red]Container is not running.[/red]")
            self.wait_for_escape()
            return
        
        console.print("\n[bright_yellow]Network Shares Available:[/bright_yellow]")
        console.print("• recordings - For TV device recordings")
        console.print("• media - General media library")
        console.print("• downloads - Downloaded VOD content")
        
        console.print("\n[bright_yellow]Connection Information:[/bright_yellow]")
        try:
            # Get server IP
            import socket
            hostname = socket.gethostname()
            server_ip = socket.gethostbyname(hostname)
            console.print(f"• Server IP: {server_ip}")
            console.print(f"• SMB Shares: \\\\{server_ip}\\[share_name]")
        except:
            console.print("• Server IP: [Use your server's IP address]")
        
        console.print("\n[bright_yellow]Supported TV Apps:[/bright_yellow]")
        console.print("• X-plore File Manager")
        console.print("• Total Commander")
        console.print("• Solid Explorer")
        console.print("• Most built-in TV file managers")
        
        self.wait_for_escape()
    
    def stop_samba_container(self):
        """Stop Samba container"""
        console.clear()
        console.print(Panel.fit("Stopping Samba Container", style="dim white"))
        
        try:
            result = subprocess.run(['docker-compose', 'stop', 'samba'], 
                                  capture_output=True, check=True, timeout=30)
            console.print("[green]✓[/green] Samba container stopped successfully!")
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error stopping container: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def start_all_containers(self):
        """Start all containers"""
        console.clear()
        console.print(Panel.fit("Starting All Containers", style="dim white"))
        
        # Check if Docker is available
        docker_status = self.check_docker_status()
        if "[green]" not in docker_status:
            console.print("[red]✗[/red] Docker is not available")
            self.wait_for_escape()
            return
        
        console.print("Starting all containers...")
        console.print("This may take a few minutes...")
        
        try:
            # Create necessary directories
            os.makedirs("jellyfin/config", exist_ok=True)
            os.makedirs("jellyfin/cache", exist_ok=True)
            os.makedirs("media", exist_ok=True)
            
            result = subprocess.run(['docker-compose', 'up', '-d'], 
                                  capture_output=True, check=True, timeout=300)
            
            console.print("[green]✓[/green] All containers started successfully!")
            console.print("\nContainer Information:")
            console.print("• NGINX-RTMP: http://localhost:8080")
            console.print("• Jellyfin: http://localhost:8096")
            
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
            if result.stderr.decode():
                console.print(f"[dim]Stderr:[/dim] {result.stderr.decode()}")
                
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error starting containers: {e}[/red]")
            if e.stdout:
                console.print(f"Stdout: {e.stdout.decode()}")
            if e.stderr:
                console.print(f"Stderr: {e.stderr.decode()}")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def stop_all_containers(self):
        """Stop all containers"""
        console.clear()
        console.print(Panel.fit("Stopping All Containers", style="dim white"))
        
        try:
            result = subprocess.run(['docker-compose', 'down'], 
                                  capture_output=True, check=True, timeout=60)
            console.print("[green]✓[/green] All containers stopped successfully!")
            console.print(f"\n[dim]Docker output:[/dim]\n{result.stdout.decode()}")
        except subprocess.CalledProcessError as e:
            console.print(f"[red]Error stopping containers: {e}[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
        
        self.wait_for_escape()
    
    def detect_os(self):
        """Detect if system is Arch Linux or Ubuntu"""
        try:
            # Check for Arch Linux
            if os.path.exists('/etc/arch-release'):
                return 'arch'
            
            # Check for Ubuntu/Debian
            if os.path.exists('/etc/lsb-release'):
                with open('/etc/lsb-release', 'r') as f:
                    content = f.read().lower()
                    if 'ubuntu' in content:
                        return 'ubuntu'
            
            # Check /etc/os-release for more distributions
            if os.path.exists('/etc/os-release'):
                with open('/etc/os-release', 'r') as f:
                    content = f.read().lower()
                    if 'arch' in content:
                        return 'arch'
                    elif 'ubuntu' in content:
                        return 'ubuntu'
            
            # Fallback: check pacman or apt
            try:
                subprocess.run(['pacman', '--version'], capture_output=True, check=True)
                return 'arch'
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            
            try:
                subprocess.run(['apt', '--version'], capture_output=True, check=True)
                return 'ubuntu'
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
            
            return 'unknown'
        except:
            return 'unknown'
    
    def install_docker(self):
        """Install Docker and Docker Compose with OS detection"""
        console.clear()
        console.print(Panel.fit("Install Docker", style="dim white"))
        
        # Detect OS
        os_type = self.detect_os()
        console.print(f"Detected OS: {os_type.upper()}")
        
        if os_type == 'unknown':
            console.print("[red]✗[/red] Unsupported operating system")
            console.print("This installer supports Arch Linux and Ubuntu only")
            self.wait_for_escape()
            return
        
        # Check if Docker is already installed
        try:
            subprocess.run(['docker', '--version'], capture_output=True, check=True)
            console.print("[yellow]Docker is already installed[/yellow]")
            
            # Check if user is in docker group
            try:
                result = subprocess.run(['groups'], capture_output=True, text=True)
                if 'docker' not in result.stdout:
                    console.print("[yellow]User not in docker group[/yellow]")
                    self._add_user_to_docker_group()
                else:
                    console.print("[green]✓[/green] User is already in docker group")
            except:
                console.print("[yellow]Could not check docker group membership[/yellow]")
            
            self.wait_for_escape()
            return
        except:
            pass
        
        console.print("\\nThis will install Docker and Docker Compose")
        console.print("The installation requires sudo privileges")
        
        try:
            key = input()
            if key == '\\x1b':  # ESC
                return
        except:
            pass
        
        if os_type == 'arch':
            self._install_docker_arch()
        elif os_type == 'ubuntu':
            self._install_docker_ubuntu()
    
    def _install_docker_arch(self):
        """Install Docker on Arch Linux"""
        console.print("\\n[bright_yellow]Installing Docker on Arch Linux...[/bright_yellow]")
        
        try:
            # Update package database
            console.print("Updating package database...")
            result = subprocess.run(['sudo', 'pacman', '-Sy'], check=True)
            
            # Install Docker
            console.print("Installing Docker...")
            result = subprocess.run(['sudo', 'pacman', '-S', '--noconfirm', 'docker', 'docker-compose'], check=True)
            console.print("[green]✓[/green] Docker and Docker Compose installed")
            
            # Enable and start Docker service
            console.print("Enabling Docker service...")
            subprocess.run(['sudo', 'systemctl', 'enable', 'docker'], check=True)
            subprocess.run(['sudo', 'systemctl', 'start', 'docker'], check=True)
            console.print("[green]✓[/green] Docker service enabled and started")
            
            # Add user to docker group
            self._add_user_to_docker_group()
            
            console.print("\\n[green]✓[/green] Docker installation completed successfully!")
            console.print("Please log out and log back in for group changes to take effect")
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Installation failed: {e}")
            console.print("Please check your sudo privileges and try again")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during installation: {e}")
        
        self.wait_for_escape()
    
    def _install_docker_ubuntu(self):
        """Install Docker on Ubuntu"""
        console.print("\\n[bright_yellow]Installing Docker on Ubuntu...[/bright_yellow]")
        
        try:
            # Update package index
            console.print("Updating package index...")
            subprocess.run(['sudo', 'apt', 'update'], check=True)
            
            # Install prerequisites
            console.print("Installing prerequisites...")
            subprocess.run(['sudo', 'apt', 'install', '-y', 
                          'apt-transport-https', 'ca-certificates', 'curl', 'gnupg', 'lsb-release'], check=True)
            
            # Add Docker's official GPG key
            console.print("Adding Docker GPG key...")
            subprocess.run(['curl', '-fsSL', 'https://download.docker.com/linux/ubuntu/gpg'], 
                         stdout=subprocess.PIPE, check=True)
            
            # Add Docker repository
            console.print("Adding Docker repository...")
            arch_result = subprocess.run(['dpkg', '--print-architecture'], capture_output=True, text=True, check=True)
            arch = arch_result.stdout.strip()
            
            lsb_result = subprocess.run(['lsb_release', '-cs'], capture_output=True, text=True, check=True)
            codename = lsb_result.stdout.strip()
            
            repo_line = f"deb [arch={arch} signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu {codename} stable"
            
            # Create keyring directory and add key
            subprocess.run(['sudo', 'mkdir', '-p', '/usr/share/keyrings'], check=True)
            key_process = subprocess.run(['curl', '-fsSL', 'https://download.docker.com/linux/ubuntu/gpg'], 
                                       stdout=subprocess.PIPE, check=True)
            subprocess.run(['sudo', 'gpg', '--dearmor', '-o', '/usr/share/keyrings/docker-archive-keyring.gpg'], 
                         input=key_process.stdout, check=True)
            
            # Add repository
            with open('/tmp/docker.list', 'w') as f:
                f.write(repo_line)
            subprocess.run(['sudo', 'mv', '/tmp/docker.list', '/etc/apt/sources.list.d/docker.list'], check=True)
            
            # Update package index again
            console.print("Updating package index with Docker repository...")
            subprocess.run(['sudo', 'apt', 'update'], check=True)
            
            # Install Docker
            console.print("Installing Docker...")
            subprocess.run(['sudo', 'apt', 'install', '-y', 'docker-ce', 'docker-ce-cli', 'containerd.io'], check=True)
            
            # Install Docker Compose
            console.print("Installing Docker Compose...")
            subprocess.run(['sudo', 'apt', 'install', '-y', 'docker-compose-plugin'], check=True)
            
            console.print("[green]✓[/green] Docker and Docker Compose installed")
            
            # Enable and start Docker service
            console.print("Enabling Docker service...")
            subprocess.run(['sudo', 'systemctl', 'enable', 'docker'], check=True)
            subprocess.run(['sudo', 'systemctl', 'start', 'docker'], check=True)
            console.print("[green]✓[/green] Docker service enabled and started")
            
            # Add user to docker group
            self._add_user_to_docker_group()
            
            console.print("\\n[green]✓[/green] Docker installation completed successfully!")
            console.print("Please log out and log back in for group changes to take effect")
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Installation failed: {e}")
            console.print("Please check your sudo privileges and internet connection")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during installation: {e}")
        
        self.wait_for_escape()
    
    def _add_user_to_docker_group(self):
        """Add current user to docker group"""
        try:
            import getpass
            username = getpass.getuser()
            console.print(f"Adding user '{username}' to docker group...")
            subprocess.run(['sudo', 'usermod', '-aG', 'docker', username], check=True)
            console.print("[green]✓[/green] User added to docker group")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Could not add user to docker group: {e}")
            console.print("You may need to run: sudo usermod -aG docker $USER")
    
    def install_lazydocker(self):
        """Install Lazydocker with OS detection"""
        console.clear()
        console.print(Panel.fit("Install Lazydocker", style="dim white"))
        
        # Check if Docker is installed
        docker_status = self.check_docker_status()
        if "[green]" not in docker_status:
            console.print("[red]✗[/red] Docker is not installed")
            console.print("Please install Docker first using the 'Install Docker' option")
            self.wait_for_escape()
            return
        
        # Detect OS
        os_type = self.detect_os()
        console.print(f"Detected OS: {os_type.upper()}")
        
        if os_type == 'unknown':
            console.print("[red]✗[/red] Unsupported operating system")
            console.print("This installer supports Arch Linux and Ubuntu only")
            self.wait_for_escape()
            return
        
        # Check if Lazydocker is already installed
        try:
            result = subprocess.run(['lazydocker', '--version'], capture_output=True, check=True)
            console.print("[yellow]Lazydocker is already installed[/yellow]")
            console.print(result.stdout.decode())
            self.wait_for_escape()
            return
        except:
            pass
        
        console.print("\\nThis will install Lazydocker - a simple terminal UI for Docker")
        console.print("The installation requires sudo privileges")
        
        try:
            key = input()
            if key == '\\x1b':  # ESC
                return
        except:
            pass
        
        if os_type == 'arch':
            self._install_lazydocker_arch()
        elif os_type == 'ubuntu':
            self._install_lazydocker_ubuntu()
    
    def _install_lazydocker_arch(self):
        """Install Lazydocker on Arch Linux"""
        console.print("\\n[bright_yellow]Installing Lazydocker on Arch Linux...[/bright_yellow]")
        
        try:
            # Check if yay is available for AUR packages
            try:
                subprocess.run(['yay', '--version'], capture_output=True, check=True)
                console.print("Installing via yay (AUR)...")
                result = subprocess.run(['yay', '-S', '--noconfirm', 'lazydocker'], check=True)
                console.print("[green]✓[/green] Lazydocker installed via AUR")
            except:
                # Fallback to manual installation
                console.print("yay not found, installing manually...")
                self._install_lazydocker_manual()
            
            console.print("\\n[green]✓[/green] Lazydocker installation completed!")
            console.print("You can now run 'lazydocker' to launch the Docker TUI")
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Installation failed: {e}")
        except Exception as e:
            console.print(f"[red]✗[/red] Error during installation: {e}")
        
        self.wait_for_escape()
    
    def _install_lazydocker_ubuntu(self):
        """Install Lazydocker on Ubuntu"""
        console.print("\\n[bright_yellow]Installing Lazydocker on Ubuntu...[/bright_yellow]")
        
        try:
            # Lazydocker is not in Ubuntu repos, so we install manually
            self._install_lazydocker_manual()
            
            console.print("\\n[green]✓[/green] Lazydocker installation completed!")
            console.print("You can now run 'lazydocker' to launch the Docker TUI")
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error during installation: {e}")
        
        self.wait_for_escape()
    
    def _install_lazydocker_manual(self):
        """Install Lazydocker manually from GitHub releases"""
        console.print("Installing Lazydocker from GitHub releases...")
        
        try:
            # Get latest release URL
            import json
            
            # Download the latest release info
            result = subprocess.run(['curl', '-s', 'https://api.github.com/repos/jesseduffield/lazydocker/releases/latest'], 
                                  capture_output=True, text=True, check=True)
            release_data = json.loads(result.stdout)
            
            # Find the appropriate binary for Linux x86_64
            download_url = None
            for asset in release_data.get('assets', []):
                if 'Linux_x86_64' in asset.get('name', '') and asset.get('name', '').endswith('.tar.gz'):
                    download_url = asset.get('browser_download_url')
                    break
            
            if not download_url:
                raise Exception("Could not find appropriate release binary")
            
            console.print(f"Downloading from: {download_url}")
            
            # Download and install
            temp_dir = '/tmp/lazydocker_install'
            os.makedirs(temp_dir, exist_ok=True)
            
            # Download
            subprocess.run(['curl', '-L', '-o', f'{temp_dir}/lazydocker.tar.gz', download_url], check=True)
            
            # Extract
            subprocess.run(['tar', '-xzf', f'{temp_dir}/lazydocker.tar.gz', '-C', temp_dir], check=True)
            
            # Install to /usr/local/bin
            subprocess.run(['sudo', 'mv', f'{temp_dir}/lazydocker', '/usr/local/bin/'], check=True)
            subprocess.run(['sudo', 'chmod', '+x', '/usr/local/bin/lazydocker'], check=True)
            
            # Cleanup
            subprocess.run(['rm', '-rf', temp_dir], check=True)
            
            console.print("[green]✓[/green] Lazydocker installed to /usr/local/bin/lazydocker")
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Download/installation failed: {e}")
            raise
        except Exception as e:
            console.print(f"[red]✗[/red] Manual installation failed: {e}")
            raise

    def launch_lazydocker(self):
        """Launch Lazydocker TUI"""
        try:
            # Check if lazydocker is installed
            subprocess.run(['which', 'lazydocker'], capture_output=True, check=True)
            
            # Clear screen and launch lazydocker
            console.clear()
            console.print("[bright_yellow]Launching Lazydocker...[/bright_yellow]\n")
            console.print("Press 'q' to exit Lazydocker and return to the menu\n")
            
            # Launch lazydocker and wait for it to complete
            subprocess.call(['lazydocker'])
            
            # After lazydocker exits, we'll automatically return to the menu
            console.clear()
            
        except subprocess.CalledProcessError:
            console.print("[red]✗[/red] Lazydocker is not installed")
            console.print("Please install it first using the 'Install Lazydocker' option")
            self.wait_for_escape()
        except Exception as e:
            console.print(f"[red]✗[/red] Error launching Lazydocker: {e}")
            self.wait_for_escape()

    def load_favorites(self):
        """Load favorites from JSON file"""
        try:
            # Check data folder location
            favorites_path = os.path.join(self.data_dir, 'favorites.json')
            if os.path.exists(favorites_path):
                with open(favorites_path, 'r') as f:
                    return json.load(f)
            # Fall back to old location for backward compatibility
            elif os.path.exists('favorites.json'):
                with open('favorites.json', 'r') as f:
                    favs = json.load(f)
                # Migrate to new location
                os.makedirs(self.data_dir, exist_ok=True)
                with open(favorites_path, 'w') as f:
                    json.dump(favs, f, indent=2)
                # Remove old file
                os.remove('favorites.json')
                return favs
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Error loading favorites: {e}")
        return []

    def save_to_favorites(self, item, item_type='live'):
        """Add item to favorites JSON"""
        try:
            # Ensure data directory exists
            os.makedirs(self.data_dir, exist_ok=True)
            
            favs = self.load_favorites()
            
            # Create favorite item
            favorite_item = {
                'stream_id': item.get('stream_id', 0),
                'name': item.get('name', 'Unknown'),
                'stream_url': item.get('stream_url', ''),
                'category': item.get('category_name', 'Uncategorized'),
                'type': item_type,
                'added': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # Check if already exists
            for existing in favs:
                if (existing.get('stream_id') == favorite_item['stream_id'] and 
                    existing.get('type') == favorite_item['type']):
                    return -1  # Already exists
            
            # Add to favorites
            favs.append(favorite_item)
            
            # Save to file in data folder
            favorites_path = os.path.join(self.data_dir, 'favorites.json')
            with open(favorites_path, 'w') as f:
                json.dump(favs, f, indent=2)
            
            # Auto-generate M3U playlist
            self.generate_m3u_playlist()
            
            return len(favs)  # Return total count
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error saving to favorites: {e}")
            return 0

    def generate_m3u_playlist(self):
        """Generate M3U playlist from favorites"""
        try:
            # Ensure directories exist
            os.makedirs(self.data_dir, exist_ok=True)
            os.makedirs('nginx/html', exist_ok=True)
            
            favs = self.load_favorites()
            
            # Generate M3U content
            m3u_content = "#EXTM3U\n"
            for fav in favs:
                category = fav.get('category', 'Uncategorized')
                name = fav.get('name', 'Unknown')
                url = fav.get('stream_url', '')
                
                m3u_content += f'#EXTINF:-1 group-title="{category}",{name}\n'
                m3u_content += f'{url}\n'
            
            # Save to both locations
            with open('nginx/html/iptv.m3u', 'w', encoding='utf-8') as f:
                f.write(m3u_content)
            
            with open(os.path.join(self.data_dir, 'iptv.m3u'), 'w', encoding='utf-8') as f:
                f.write(m3u_content)
                
            return True
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error generating M3U playlist: {e}")
            return False

    def is_favorite(self, item, item_type='live'):
        """Check if an item is in favorites"""
        try:
            favs = self.load_favorites()
            stream_id = item.get('stream_id', 0)
            
            for existing in favs:
                if (existing.get('stream_id') == stream_id and 
                    existing.get('type') == item_type):
                    return True
            return False
        except:
            return False
    
    def remove_from_favorites(self, item, item_type='live'):
        """Remove item from favorites"""
        try:
            favs = self.load_favorites()
            stream_id = item.get('stream_id', 0)
            
            # Find and remove the item
            original_count = len(favs)
            favs = [f for f in favs if not (f.get('stream_id') == stream_id and f.get('type') == item_type)]
            
            if len(favs) < original_count:
                # Save updated favorites to data folder
                favorites_path = os.path.join(self.data_dir, 'favorites.json')
                with open(favorites_path, 'w') as f:
                    json.dump(favs, f, indent=2)
                
                # Regenerate M3U playlist
                self.generate_m3u_playlist()
                
                return len(favs)  # Return new count
            
            return -1  # Item not found
            
        except Exception as e:
            console.print(f"[red]✗[/red] Error removing from favorites: {e}")
            return 0
    
    def get_favorites_set(self):
        """Get favorites as a set for quick lookups"""
        try:
            favs = self.load_favorites()
            return {(f.get('stream_id'), f.get('type')) for f in favs}
        except:
            return set()

    def get_epg_data(self, stream_id, channel_name=None, limit=3):
        """Get EPG data for a stream using multiple strategies"""
        
        def try_epg_fetch(param_value):
            """Helper function to try fetching EPG with a given parameter"""
            try:
                url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_short_epg&stream_id={param_value}&limit={limit}"
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    epg_data = response.json()
                    listings = epg_data.get('epg_listings', []) if isinstance(epg_data, dict) else []
                    if listings:
                        return listings
            except:
                pass
            return []
        
        # Strategy 1: Try with stream_id first
        epg_listings = try_epg_fetch(stream_id)
        if epg_listings:
            return epg_listings
        
        # Strategy 2: If channel name is provided, try to find base channel name
        if channel_name:
            # Remove common HD/SD suffixes and try again
            base_name = channel_name
            
            # Remove HD/FHD/SD/4K suffixes
            for suffix in [' HD', ' FHD', ' SD', ' 4K', ' UHD', ' ᴴᴰ', ' (HD)', ' [HD]']:
                if base_name.endswith(suffix):
                    base_name = base_name[:-len(suffix)].strip()
                    break
            
            # Also try removing trailing numbers (like "SUPER ECRAN 2")
            import re
            base_name_no_number = re.sub(r'\s+\d+$', '', base_name).strip()
            
            # Try with base name variants
            if base_name != channel_name:
                # First try the base name without HD/SD suffix
                epg_listings = try_epg_fetch(base_name)
                if epg_listings:
                    return epg_listings
                
                # Try base name without number
                if base_name_no_number != base_name:
                    epg_listings = try_epg_fetch(base_name_no_number)
                    if epg_listings:
                        return epg_listings
            
            # Strategy 3: Try to find a matching stream with similar name from database
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                
                # Look for channels with similar base names
                cursor.execute("""
                    SELECT DISTINCT stream_id, name 
                    FROM live_streams 
                    WHERE name LIKE ? 
                    ORDER BY 
                        CASE 
                            WHEN name = ? THEN 0
                            WHEN name LIKE ? THEN 1
                            ELSE 2
                        END
                    LIMIT 10
                """, (f'%{base_name_no_number}%', base_name, f'{base_name}%'))
                
                similar_channels = cursor.fetchall()
                conn.close()
                
                # Try each similar channel's stream_id
                for similar_id, similar_name in similar_channels:
                    if similar_id != stream_id:  # Don't retry the same stream_id
                        epg_listings = try_epg_fetch(similar_id)
                        if epg_listings:
                            console.print(f"[dim yellow]EPG found using similar channel: {similar_name}[/dim yellow]")
                            return epg_listings
            except:
                pass
        
        return []

    def _decode_base64_if_needed(self, text):
        """Decode base64 text if it appears to be encoded"""
        if not text:
            return None
        try:
            if all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=' for c in text.strip()):
                return base64.b64decode(text).decode('utf-8', errors='ignore')
        except:
            pass
        return text

    def get_now_playing(self, stream_id, channel_name=None):
        """Get currently playing program title and description for a channel"""
        epg_data = self.get_epg_data(stream_id, channel_name=channel_name, limit=1)
        if epg_data and len(epg_data) > 0:
            program = epg_data[0]
            title = self._decode_base64_if_needed(program.get('title', ''))
            description = self._decode_base64_if_needed(program.get('description', ''))

            return {
                'title': title if title else None,
                'description': description if description else None
            }
        return None

    def get_now_playing_local(self, stream_id, channel_name=None, fetch_if_missing=False):
        """Get currently playing program from local EPG cache, optionally fetch if missing"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            now = int(time.time())
            cache_max_age = 6 * 3600  # 6 hours cache expiry

            # Check for cached EPG data that's not expired
            cursor.execute("""
                SELECT title, description, cached_at FROM epg
                WHERE stream_id = ? AND start_time <= ? AND end_time > ?
                ORDER BY start_time DESC
                LIMIT 1
            """, (stream_id, now, now))

            row = cursor.fetchone()

            if row:
                cached_at = row[2] or 0
                # Return cached data if not expired
                if (now - cached_at) < cache_max_age:
                    conn.close()
                    return {
                        'title': row[0] if row[0] else None,
                        'description': row[1] if row[1] else None
                    }

            conn.close()

            # If fetch_if_missing is True, fetch from network and cache
            if fetch_if_missing:
                return self._fetch_and_cache_epg(stream_id, channel_name)

        except:
            pass
        return None

    def _fetch_and_cache_epg(self, stream_id, channel_name=None):
        """Fetch EPG from network and cache in database"""
        try:
            # Fetch from network
            epg_data = self.get_epg_data(stream_id, channel_name=channel_name, limit=10)

            if epg_data:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                now = int(time.time())

                for listing in epg_data:
                    try:
                        start_time = int(listing.get('start_timestamp', 0))
                        end_time = int(listing.get('stop_timestamp', 0))
                        title = self._decode_base64_if_needed(listing.get('title', '')) or listing.get('title', '')
                        description = self._decode_base64_if_needed(listing.get('description', '')) or listing.get('description', '')

                        if start_time and end_time:
                            cursor.execute('''
                                INSERT OR REPLACE INTO epg (stream_id, start_time, end_time, title, description, cached_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (stream_id, start_time, end_time, title, description, now))
                    except:
                        pass

                conn.commit()
                conn.close()

                # Return the current program
                for listing in epg_data:
                    start_time = int(listing.get('start_timestamp', 0))
                    end_time = int(listing.get('stop_timestamp', 0))
                    if start_time <= now < end_time:
                        title = self._decode_base64_if_needed(listing.get('title', '')) or listing.get('title', '')
                        description = self._decode_base64_if_needed(listing.get('description', '')) or listing.get('description', '')
                        return {
                            'title': title if title else None,
                            'description': description if description else None
                        }
        except:
            pass
        return None

    def get_epg_with_upcoming(self, stream_id):
        """Get current and next program from EPG cache"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            now = int(time.time())

            # Get current program (now playing)
            cursor.execute("""
                SELECT title, description, start_time, end_time FROM epg
                WHERE stream_id = ? AND start_time <= ? AND end_time > ?
                ORDER BY start_time DESC
                LIMIT 1
            """, (stream_id, now, now))
            current_row = cursor.fetchone()

            # Get next program (upcoming)
            cursor.execute("""
                SELECT title, description, start_time, end_time FROM epg
                WHERE stream_id = ? AND start_time > ?
                ORDER BY start_time ASC
                LIMIT 1
            """, (stream_id, now))
            next_row = cursor.fetchone()

            conn.close()

            result = {'now': None, 'next': None}

            if current_row:
                result['now'] = {
                    'title': current_row[0] if current_row[0] else None,
                    'description': current_row[1] if current_row[1] else None,
                    'start_time': current_row[2],
                    'end_time': current_row[3]
                }

            if next_row:
                result['next'] = {
                    'title': next_row[0] if next_row[0] else None,
                    'description': next_row[1] if next_row[1] else None,
                    'start_time': next_row[2],
                    'end_time': next_row[3]
                }

            return result
        except:
            pass
        return {'now': None, 'next': None}

    # ========================
    # YouTube Tool Methods
    # ========================

    def youtube_tool_menu(self):
        """Main YouTube Tool interface with search"""
        while True:
            console.clear()
            console.print(Panel.fit("YouTube Video Search & Download Tool", style="dim white"))
            console.print()

            options = [
                "Search YouTube Videos",
                "Back to Main Menu"
            ]

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )

            choice = terminal_menu.show()

            if choice is None or choice == 1:  # ESC or Back
                break
            elif choice == 0:  # Search
                console.clear()
                console.print(Panel.fit("YouTube Search", style="dim white"))
                console.print()
                query = input("Enter search query (or press Enter to cancel): ").strip()

                if query:
                    self.youtube_search_results_menu(query)

    def search_youtube(self, query, max_results=20):
        """Search YouTube videos using yt-dlp"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'force_generic_extractor': False,
            }

            search_query = f"ytsearch{max_results}:{query}"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                console.print(f"[cyan]Searching YouTube for: {query}...[/cyan]")
                result = ydl.extract_info(search_query, download=False)

                if result and 'entries' in result:
                    videos = []
                    for entry in result['entries']:
                        if entry:
                            videos.append({
                                'id': entry.get('id'),
                                'title': entry.get('title', 'Unknown Title'),
                                'uploader': entry.get('uploader', 'Unknown Uploader'),
                                'duration': entry.get('duration', 0),
                                'url': f"https://www.youtube.com/watch?v={entry.get('id')}"
                            })
                    return videos
                return []

        except Exception as e:
            console.print(f"[red]Error searching YouTube: {str(e)}[/red]")
            return []

    def youtube_search_results_menu(self, query):
        """Display YouTube search results with navigation"""
        videos = self.search_youtube(query)

        if not videos:
            console.print("[yellow]No videos found![/yellow]")
            self.wait_for_escape()
            return

        while True:
            console.clear()
            console.print(Panel.fit(f"Search Results: {query}", style="dim white"))
            console.print(f"Found {len(videos)} videos\n")

            # Format video options for menu
            options = []
            for video in videos:
                if video['duration']:
                    duration = int(video['duration'])
                    duration_str = f"{duration//60}:{duration%60:02d}"
                else:
                    duration_str = "Live"
                title_truncated = video['title'][:70] + "..." if len(video['title']) > 70 else video['title']
                uploader_truncated = video['uploader'][:25] + "..." if len(video['uploader']) > 25 else video['uploader']
                options.append(f"{title_truncated} | {uploader_truncated} | [{duration_str}]")

            options.append("Back to Search")

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )

            choice = terminal_menu.show()

            if choice is None or choice == len(videos):  # ESC or Back
                break
            else:
                # Open video action menu
                self.youtube_video_action_menu(videos[choice])

    def youtube_video_action_menu(self, video):
        """Action menu for selected YouTube video"""
        while True:
            console.clear()

            # Fetch full video info for better details
            full_info = self.get_youtube_video_info(video['url'])
            if full_info:
                video.update(full_info)

            # Display video info
            console.print(Panel.fit(f"Video: {video['title']}", style="dim white"))
            console.print(f"Uploader: {video.get('uploader', 'Unknown')}")
            if video.get('duration'):
                duration = int(video['duration'])
                duration_str = f"{duration//60}:{duration%60:02d}"
                console.print(f"Duration: {duration_str}")
            if video.get('view_count'):
                console.print(f"Views: {video['view_count']:,}")
            console.print()

            options = [
                "Play Video (P)",
                "Show Info (I)",
                "Download Video (D)",
                "Back to Results"
            ]

            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )

            choice = terminal_menu.show()

            if choice is None or choice == 3:  # ESC or Back
                break
            elif choice == 0:  # Play
                self.play_youtube_video(video)
            elif choice == 1:  # Info
                self.show_youtube_video_info(video)
            elif choice == 2:  # Download
                self.download_youtube_video(video)

    def get_youtube_video_info(self, url):
        """Get detailed video information"""
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'id': info.get('id'),
                    'title': info.get('title'),
                    'description': info.get('description'),
                    'uploader': info.get('uploader'),
                    'duration': info.get('duration'),
                    'view_count': info.get('view_count'),
                    'like_count': info.get('like_count'),
                    'upload_date': info.get('upload_date'),
                    'url': url
                }
        except Exception as e:
            console.print(f"[red]Error fetching video info: {str(e)}[/red]")
            return None

    def show_youtube_video_info(self, video):
        """Display detailed video information"""
        console.clear()

        # Create info panel
        info_lines = []
        info_lines.append(f"[bold]Title:[/bold] {video.get('title', 'Unknown')}")
        info_lines.append(f"[bold]Uploader:[/bold] {video.get('uploader', 'Unknown')}")

        if video.get('duration'):
            duration = int(video['duration'])
            duration_str = f"{duration//60}:{duration%60:02d}"
            info_lines.append(f"[bold]Duration:[/bold] {duration_str}")

        if video.get('view_count'):
            info_lines.append(f"[bold]Views:[/bold] {video['view_count']:,}")

        if video.get('like_count'):
            info_lines.append(f"[bold]Likes:[/bold] {video['like_count']:,}")

        if video.get('upload_date'):
            date_str = video['upload_date']
            formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            info_lines.append(f"[bold]Upload Date:[/bold] {formatted_date}")

        info_lines.append(f"[bold]URL:[/bold] {video.get('url', 'Unknown')}")
        info_lines.append("")
        info_lines.append("[bold]Description:[/bold]")

        description = video.get('description', 'No description available')
        # Limit description length
        if len(description) > 500:
            description = description[:500] + "..."
        info_lines.append(description)

        console.print(Panel("\n".join(info_lines), title="Video Information", style="cyan"))
        self.wait_for_escape()

    def play_youtube_video(self, video):
        """Play YouTube video with MPV"""
        console.clear()
        console.print(Panel.fit(f"Playing: {video['title']}", style="dim white"))

        try:
            # Test MPV availability
            result = subprocess.run(['mpv', '--version'], capture_output=True, check=True, timeout=5)
            console.print("[green]✓[/green] MPV is available")

        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("[red]✗[/red] MPV not found. Install MPV to play videos.")
            console.print("\nInstall instructions:")
            console.print("Ubuntu/Debian: sudo apt install mpv")
            console.print("macOS: brew install mpv")
            console.print(f"\nVideo URL: {video['url']}")
            self.wait_for_escape()
            return
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] MPV check timed out, trying to play anyway...")

        console.print(f"Video URL: {video['url']}")
        console.print()

        try:
            console.print("Starting MPV player...")
            console.print("Press 'q' in MPV to stop playback")
            console.print()

            # Use yt-dlp with MPV for best quality with simple buffering
            mpv_cmd = ['mpv', '--ytdl-format=bestvideo[height<=1080]+bestaudio/best']

            # Add platform-specific hardware acceleration
            if self.is_raspberry_pi():
                # Raspberry Pi 4 with Wayland
                mpv_cmd.extend([
                    '--hwdec=v4l2m2m',                   # Pi 4 hardware decoder
                    '--vo=dmabuf-wayland',               # Native Wayland output
                    '--gpu-context=wayland',             # Wayland GPU context
                ])
            else:
                # Other platforms - auto detection
                mpv_cmd.extend([
                    '--hwdec=auto',                      # Auto hardware decoding
                    '--vo=gpu',                          # GPU video output
                ])

            # Common settings for all platforms
            mpv_cmd.extend([
                '--cache=yes',                           # Enable cache
                '--demuxer-max-bytes=100M',              # Cache up to 100MB for YT
                '--demuxer-max-back-bytes=50M',          # Backward cache 50MB
                '--network-timeout=60',                  # 60 second timeout
                '--keep-open=yes',                       # Keep window open
                '--osd-level=1',                         # Show OSD messages
                video['url']
            ])

            # Run MPV
            subprocess.run(mpv_cmd)

        except KeyboardInterrupt:
            console.print("\n[yellow]Playback interrupted[/yellow]")
        except Exception as e:
            console.print(f"[red]Error playing video: {str(e)}[/red]")
            self.wait_for_escape()

    def download_youtube_video(self, video):
        """Download YouTube video"""
        console.clear()
        console.print(Panel.fit(f"Download: {video['title']}", style="dim white"))
        console.print()

        # Create youtube download directory
        youtube_dir = os.path.join(self.data_dir, "youtube")
        os.makedirs(youtube_dir, exist_ok=True)

        # Ask for format preference
        console.print("Select download format:")
        format_options = [
            "Best Quality (1080p MOV - DaVinci Resolve Compatible)",
            "Audio Only (MP3)",
            "720p Video (MOV - DaVinci Resolve Compatible)",
            "Cancel"
        ]

        terminal_menu = TerminalMenu(
            format_options,
            title="",
            menu_cursor="> "
        )

        format_choice = terminal_menu.show()

        if format_choice is None or format_choice == 3:  # Cancel
            return

        # Set yt-dlp options based on choice
        if format_choice == 0:  # Best quality
            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio/best',
                'outtmpl': os.path.join(youtube_dir, '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mov',
                }],
            }
        elif format_choice == 1:  # Audio only
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(youtube_dir, '%(title)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
        else:  # 720p
            ydl_opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best',
                'outtmpl': os.path.join(youtube_dir, '%(title)s.%(ext)s'),
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mov',
                }],
            }

        # Add progress hook
        def progress_hook(d):
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', 'N/A')
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                console.print(f"\r[cyan]Downloading:[/cyan] {percent} | Speed: {speed} | ETA: {eta}", end='')
            elif d['status'] == 'finished':
                console.print(f"\n[green]✓[/green] Download complete! Processing...")

        ydl_opts['progress_hooks'] = [progress_hook]

        try:
            console.print(f"[cyan]Starting download...[/cyan]\n")

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video['url']])

            console.print(f"\n[green]✓[/green] Video downloaded successfully to: {youtube_dir}")
            console.print(f"[dim]File saved in: {youtube_dir}[/dim]")

        except Exception as e:
            console.print(f"\n[red]Error downloading video: {str(e)}[/red]")

        self.wait_for_escape()

def main():
    """Main entry point"""
    try:
        # Check if MPV is available
        subprocess.run(['mpv', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("Warning: MPV not found. Install it to play streams.")
        console.print("Ubuntu/Debian: sudo apt install mpv")
        console.print("macOS: brew install mpv")
        input()
    
    manager = IPTVMenuManager()
    manager.main_menu()

if __name__ == "__main__":
    main()