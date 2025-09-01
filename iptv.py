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
import json
import subprocess
import signal
import glob
import re
import base64
from datetime import datetime
from dotenv import load_dotenv
from simple_term_menu import TerminalMenu
from rich.console import Console
from pyfiglet import Figlet
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()

# Load environment variables
load_dotenv()

class IPTVMenuManager:
    def __init__(self):
        self.db_path = "iptv.db"
        
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
        
    def wait_for_escape(self):
        """Wait for escape key instead of enter"""
        import termios, sys, tty
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            console.print("\nPress [dim white]Escape[/dim white] to continue...")
            while True:
                char = sys.stdin.read(1)
                if ord(char) == 27:  # ESC key
                    break
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except:
            # Fallback for environments where termios doesn't work
            console.print("\nPress [dim white]Enter[/dim white] to continue...")
            input()
        
    def main_menu(self):
        """Main menu with arrow key navigation"""
        while True:
            console.clear()
            console.print()
            console.print("[bright_red] ✻[/bright_red] Welcome to")
            
            # Create figlet title
            figlet = Figlet(font='isometric1')
            title = figlet.renderText('IPTV')
            console.print(f"[cyan]{title}[/cyan]")
            console.print()
            
            # Show database status
            self.show_status()
            
            options = [
                "Search IPTV",
                "Browse Categories",
                "Database Statistics",
                "Update IPTV db",
                "Install & Manage Tools"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC pressed
                console.print("\nGoodbye!")
                break
            elif choice == 0:  # Search IPTV
                self.unified_search_menu()
            elif choice == 1:  # Browse Categories
                self.browse_categories_menu()
            elif choice == 2:  # Statistics
                self.show_statistics()
            elif choice == 3:  # Update IPTV db
                self.download_menu()
            elif choice == 4:  # Install & Manage Tools
                self.container_management_menu()
    
    def show_status(self):
        """Show current database status"""
        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                live_count = cursor.execute("SELECT COUNT(*) FROM live_streams").fetchone()[0]
                vod_count = cursor.execute("SELECT COUNT(*) FROM vod_streams").fetchone()[0]
                conn.close()
                
                status = f"[green]●[/green] Database: Ready | Live: {live_count:,} | VOD: {vod_count:,}"
                console.print(Panel(status, style="dim white"))
            except:
                console.print(Panel("[dim white]●[/dim white] Database: Error reading", style="dim white"))
        else:
            console.print(Panel("[dim white]●[/dim white] Database: Not found - Use 'Download/Update Database'", style="dim white"))
    
    def download_menu(self):
        """Download/Update menu"""
        while True:
            console.clear()
            console.print(Panel.fit("Download/Update Database", style="dim white"))
            
            options = [
                "Download Fresh Data (Full Update)",
                "Quick Update (Live Streams Only)",
                "Download VOD Only",
                "Back to Main Menu"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == 3:  # Back
                break
            elif choice == 0:  # Full download
                self.download_full()
            elif choice == 1:  # Quick update
                self.download_live_only()
            elif choice == 2:  # VOD only
                self.download_vod_only()
    
    def unified_search_menu(self):
        """Unified search menu for both live channels and VOD content"""
        if not self.check_database():
            return
        
        console.clear()
        console.print(Panel.fit("Enter your search term", style="dim white"))
        
        try:
            search_term = input("\n > ").strip()
            if not search_term:
                return
            
            # Search both live channels and VOD content
            live_results = self.search_live_channels(search_term)
            vod_results = self.search_vod_content(search_term)
            
            if not live_results and not vod_results:
                console.print(f"\nNo content found for '{search_term}'")
                self.wait_for_escape()
                return
            
            self.show_unified_results(live_results, vod_results, search_term)
            
        except KeyboardInterrupt:
            return
    
    def show_unified_results(self, live_results, vod_results, search_term):
        """Show unified search results with live channels and VOD content"""
        while True:
            console.clear()
            console.print(Panel.fit(f"Search Results: '{search_term}' ({len(live_results + vod_results)} found)", style="dim white"))
            console.print("[dim white]Press: Enter=Select | S=Save to favorites | D=Delete from favorites | Esc=Back[/dim white]\n")
            
            options = []
            all_results = []
            
            # Get current favorites for checking
            favorites_set = self.get_favorites_set()
            
            # Add live channel results with [LIVE] prefix and favorite indicator
            for result in live_results:
                category = result['category_name'] or 'Unknown'
                is_fav = (result.get('stream_id'), 'live') in favorites_set
                fav_indicator = "♥ " if is_fav else "  "
                option = f"{fav_indicator}[LIVE] {result['name'][:43]} | {category[:12]} | ID: {result['stream_id']}"
                options.append(option)
                all_results.append(('live', result))
            
            # Add VOD results with [VOD] prefix and favorite indicator
            for result in vod_results:
                year = result['year'] or 'N/A'
                rating = f"{result['rating']:.1f}" if result['rating'] else 'N/A'
                genre = result['genre'][:12] if result['genre'] else 'Unknown'
                # Add category_name for consistency with favorites
                result['category_name'] = f"VOD/{genre}" if genre else 'VOD'
                is_fav = (result.get('stream_id'), 'vod') in favorites_set
                fav_indicator = "♥ " if is_fav else "  "
                option = f"{fav_indicator}[VOD] {result['name'][:35]} ({year}) | {rating} | {genre}"
                options.append(option)
                all_results.append(('vod', result))
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "s", "d"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None or choice == len(all_results):  # Back
                break
            
            if 0 <= choice < len(all_results):
                result_type, selected = all_results[choice]
                
                # Handle shortcuts
                if chosen_key == 's':  # Save to favorites
                    self.save_to_favorites(selected, result_type)
                    continue  # Refresh menu immediately
                    
                elif chosen_key == 'd':  # Delete from favorites
                    self.remove_from_favorites(selected, result_type)
                    continue  # Refresh menu immediately
                    
                else:  # Enter key - show action menu
                    if result_type == 'live':
                        self.live_stream_action_menu(selected)
                    else:  # VOD
                        self.vod_action_menu(selected)
    
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
        
        sql = """
            SELECT name, category_name, stream_id, stream_url, epg_channel_id
            FROM live_streams 
            WHERE name LIKE ? 
            ORDER BY name 
            LIMIT 50
        """
        
        results = cursor.execute(sql, (f'%{query}%',)).fetchall()
        conn.close()
        
        return [dict(zip(['name', 'category_name', 'stream_id', 'stream_url', 'epg_channel_id'], row)) for row in results]
    
    def show_live_results(self, results, search_term):
        """Show live channel results with arrow navigation"""
        while True:
            console.clear()
            console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found)", style="dim white"))
            console.print("[dim white]Press: Enter=Select | S=Save to favorites | D=Delete from favorites | Esc=Back[/dim white]\n")
            
            # Get current favorites for checking
            favorites_set = self.get_favorites_set()
            
            # Create menu options from results
            options = []
            for i, result in enumerate(results):
                category = result['category_name'] or 'Unknown'
                is_fav = (result.get('stream_id'), 'live') in favorites_set
                fav_indicator = "♥ " if is_fav else "  "
                option = f"{fav_indicator}{result['name'][:48]} | {category[:15]} | ID: {result['stream_id']}"
                options.append(option)
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "s", "d"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None or choice == len(results):  # Back
                break
            
            if 0 <= choice < len(results):
                selected = results[choice]
                
                # Handle shortcuts
                if chosen_key == 's':  # Save to favorites
                    self.save_to_favorites(selected, 'live')
                    continue  # Refresh menu immediately
                    
                elif chosen_key == 'd':  # Delete from favorites
                    self.remove_from_favorites(selected, 'live')
                    continue  # Refresh menu immediately
                    
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
                "Watch Stream",
                "Stream Information", 
                "Restream",
                "Save to Favorites",
                "Copy Stream URL",
                "Back to Results"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == 5:  # Back
                break
            elif choice == 0:  # Watch
                self.play_with_mpv(channel)
            elif choice == 1:  # Info
                self.show_live_stream_info(channel)
            elif choice == 2:  # Restream
                self.restream_placeholder(channel)
            elif choice == 3:  # Save to Favorites
                result = self.save_to_favorites(channel, 'live')
                if result == -1:
                    console.print("[yellow]⚠[/yellow] Already in favorites!")
                elif result > 0:
                    console.print(f"[green]✓[/green] Added to favorites ({result} total)")
                else:
                    console.print("[red]✗[/red] Failed to add to favorites")
                self.wait_for_escape()
            elif choice == 4:  # Copy URL
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
                "Watch VOD",
                "Download VOD",
                "VOD Information",
                "Restream",
                "Save to Favorites",
                "Copy Stream URL",
                "Back to Results"
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
                self.restream_placeholder(vod_item)
            elif choice == 4:  # Save to Favorites
                result = self.save_to_favorites(vod_item, 'vod')
                if result == -1:
                    console.print("[yellow]⚠[/yellow] Already in favorites!")
                elif result > 0:
                    console.print(f"[green]✓[/green] Added to favorites ({result} total)")
                else:
                    console.print("[red]✗[/red] Failed to add to favorites")
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
        
        sql = """
            SELECT stream_id, name, year, rating, genre, stream_url
            FROM vod_streams 
            WHERE name LIKE ? 
            ORDER BY name 
            LIMIT 50
        """
        
        results = cursor.execute(sql, (f'%{query}%',)).fetchall()
        conn.close()
        
        return [dict(zip(['stream_id', 'name', 'year', 'rating', 'genre', 'stream_url'], row)) for row in results]
    
    def show_vod_results(self, results, search_term):
        """Show VOD results with arrow navigation"""
        while True:
            console.clear()
            console.print(Panel.fit(f"VOD Content: '{search_term}' ({len(results)} found)", style="dim white"))
            console.print("[dim white]Press: Enter=Select | S=Save to favorites | D=Delete from favorites | Esc=Back[/dim white]\n")
            
            # Get current favorites for checking
            favorites_set = self.get_favorites_set()
            
            # Create menu options from results
            options = []
            for result in results:
                year = result['year'] or 'N/A'
                rating = f"{result['rating']:.1f}" if result['rating'] else 'N/A'
                genre = result['genre'][:15] if result['genre'] else 'Unknown'
                # Add category_name for consistency with favorites
                result['category_name'] = f"VOD/{genre}" if genre else 'VOD'
                is_fav = (result.get('stream_id'), 'vod') in favorites_set
                fav_indicator = "♥ " if is_fav else "  "
                option = f"{fav_indicator}{result['name'][:38]} ({year}) | {rating} | {genre}"
                options.append(option)
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "s", "d"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None or choice == len(results):  # Back
                break
            
            if 0 <= choice < len(results):
                selected = results[choice]
                
                # Handle shortcuts
                if chosen_key == 's':  # Save to favorites
                    self.save_to_favorites(selected, 'vod')
                    continue  # Refresh menu immediately
                    
                elif chosen_key == 'd':  # Delete from favorites
                    self.remove_from_favorites(selected, 'vod')
                    continue  # Refresh menu immediately
                    
                else:  # Enter key - show action menu or play
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
    
    def browse_categories_menu(self):
        """Browse categories menu"""
        if not self.check_database():
            return
        
        console.clear()
        console.print(Panel.fit("Browse Categories", style="dim white"))
        
        options = [
            "Live TV Categories",
            "VOD Categories", 
            "Back to Main Menu"
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
                f"Inject Server URL: {self.inject_server or 'Not set'}",
                "Test MPV Installation",
                "Database Information", 
                "Back to Main Menu"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == 3:  # Back
                break
            elif choice == 0:  # Set inject server
                self.set_inject_server()
            elif choice == 1:  # Test MPV
                self.test_mpv()
            elif choice == 2:  # Database info
                self.show_database_info()
    
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
            
            # Create MPV command with better error handling - SIMPLIFIED VERSION
            mpv_cmd = [
                'mpv',
                channel['stream_url']
            ]
            
            # Debug: Print exact command being executed
            console.print(f"[dim white]Debug - Exact command: {' '.join(mpv_cmd)}[/dim white]")
            console.print(f"[dim white]Debug - Command length: {len(mpv_cmd)}[/dim white]")
            console.print(f"[dim white]Debug - Each arg: {mpv_cmd}[/dim white]")
            
            # Start MPV process
            process = subprocess.Popen(
                mpv_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            console.print(f"[green]✓[/green] MPV started with PID: {process.pid}")
            console.print()
            console.print("If MPV doesn't start or fails:")
            console.print("1. Check your internet connection")
            console.print("2. Verify the stream URL is accessible")
            console.print("3. Try the URL directly in a browser")
            console.print()
            console.print(f"Manual command: mpv '{channel['stream_url']}'")
            
            # Wait a moment to see if process starts successfully
            import time
            time.sleep(2)
            
            # Check if process is still running
            if process.poll() is None:
                console.print("[green]✓[/green] MPV process is running")
            else:
                # Process ended, get error output
                stdout, stderr = process.communicate()
                console.print(f"[red]✗[/red] MPV exited with code: {process.returncode}")
                if stderr:
                    console.print(f"Error: {stderr.decode().strip()}")
                if stdout:
                    console.print(f"Output: {stdout.decode().strip()}")
            
            self.wait_for_escape()
            
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
                    # Format times
                    try:
                        from datetime import datetime
                        start_dt = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
                        end_dt = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
                        time_str = f"{start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
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
        console.print("Press Escape to cancel, or any other key to continue...")
        
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
            # Transcode for lower bandwidth
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
            console.print("Mode: Transcoding (Lower Bandwidth)")
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
            
            # Save process info for later stopping
            with open(f".restream_{stream_key}.pid", "w") as f:
                f.write(str(process.pid))
                
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
        
        pid_files = glob.glob(".restream_*.pid")
        
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
        """Show VOD categories - placeholder"""
        console.print("VOD categories feature coming soon...")
        self.wait_for_escape()
    
    def show_statistics(self):
        """Show database statistics"""
        if not self.check_database():
            return
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            live_count = cursor.execute("SELECT COUNT(*) FROM live_streams").fetchone()[0]
            vod_count = cursor.execute("SELECT COUNT(*) FROM vod_streams").fetchone()[0]
            
            # Get account info if available
            try:
                account = cursor.execute("SELECT * FROM account_info").fetchone()
            except:
                account = None
            
            conn.close()
            
            console.clear()
            console.print(Panel.fit("Database Statistics", style="dim white"))
            
            table = Table(show_header=True, header_style="dim white")
            table.add_column("Metric", style="dim white")
            table.add_column("Value", style="dim white")
            
            table.add_row("Live Channels", f"{live_count:,}")
            table.add_row("VOD Content", f"{vod_count:,}")
            table.add_row("Total Content", f"{live_count + vod_count:,}")
            
            if account:
                exp_date = datetime.fromtimestamp(int(account[2])).strftime('%Y-%m-%d')
                table.add_row("Account Status", account[1])
                table.add_row("Expires", exp_date)
                table.add_row("Max Connections", account[3])
            
            console.print(table)
            
        except Exception as e:
            console.print(f"Error reading statistics: {e}")
        
        self.wait_for_escape()
    
    def container_management_menu(self):
        """Container management selection menu"""
        while True:
            console.clear()
            console.print(Panel.fit("Install & Manage Tools", style="dim white"))
            
            # Show status of both containers and Docker
            docker_status = self.check_docker_status()
            nginx_status = self.check_container_status()
            jellyfin_status = self.check_jellyfin_status()
            
            console.print(f"Docker: {docker_status}")
            console.print(f"NGINX-RTMP: {nginx_status}")
            console.print(f"Jellyfin: {jellyfin_status}")
            console.print()
            
            # Build dynamic options based on what's installed
            options = ["Install Docker", "Install Lazydocker"]
            
            # Check if lazydocker is installed to show launch option
            try:
                subprocess.run(['which', 'lazydocker'], capture_output=True, check=True)
                options.append("Launch Lazydocker")
                lazydocker_installed = True
            except:
                lazydocker_installed = False
            
            # Add remaining options
            options.extend([
                "Container Status & URLs",
                "Build & Start All Containers"
            ])
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC pressed
                break
            elif choice == 0:  # Install Docker
                self.install_docker()
            elif choice == 1:  # Install Lazydocker
                self.install_lazydocker()
            elif lazydocker_installed and choice == 2:  # Launch Lazydocker
                self.launch_lazydocker()
            elif (lazydocker_installed and choice == 3) or (not lazydocker_installed and choice == 2):  # Status & URLs
                self.show_container_status_and_urls()
            elif (lazydocker_installed and choice == 4) or (not lazydocker_installed and choice == 3):  # Build & Start All
                self.build_and_start_all_containers()
    
    def show_container_status_and_urls(self):
        """Show combined container status and URLs for both NGINX and Jellyfin"""
        console.clear()
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
        console.print()
        console.print("Press Enter to continue, or Escape to cancel...")
        
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
            "vod_categories", "vod_streams", "series_categories"
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
                with open("account_info.json", "w") as f:
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
                with open("live_categories.json", "w") as f:
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
                with open("live_streams.json", "w") as f:
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
                with open("vod_categories.json", "w") as f:
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
                with open("vod_streams.json", "w") as f:
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
                with open("series_categories.json", "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
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
                CREATE TABLE account_info (
                    username TEXT,
                    status TEXT,
                    exp_date INTEGER,
                    max_connections TEXT
                )
            ''')
            
            # Load data from JSON files
            self._load_data_from_json(cursor)
            
            # Create indexes
            cursor.execute("CREATE INDEX idx_live_name ON live_streams(name)")
            cursor.execute("CREATE INDEX idx_vod_name ON vod_streams(name)")
            
            conn.commit()
            conn.close()
            
            return True
            
        except Exception as e:
            console.print(f"Database creation error: {e}")
            return False
    
    def _load_data_from_json(self, cursor):
        """Load data from JSON files into database"""
        # Load account info
        if os.path.exists("account_info.json"):
            with open("account_info.json") as f:
                data = json.load(f)
                user_info = data.get('user_info', {})
                cursor.execute('''
                    INSERT INTO account_info VALUES (?, ?, ?, ?)
                ''', (user_info.get('username'), user_info.get('status'),
                     user_info.get('exp_date'), user_info.get('max_connections')))
        
        # Load live categories map
        categories = {}
        if os.path.exists("live_categories.json"):
            with open("live_categories.json") as f:
                cats = json.load(f)
                for cat in cats:
                    categories[cat.get('category_id')] = cat.get('category_name')
        
        # Load live streams
        if os.path.exists("live_streams.json"):
            with open("live_streams.json") as f:
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
        if os.path.exists("vod_streams.json"):
            with open("vod_streams.json") as f:
                streams = json.load(f)
                for stream in streams:
                    container_ext = stream.get('container_extension', 'mp4')
                    stream_url = f"{self.server}/movie/{self.username}/{self.password}/{stream.get('stream_id')}.{container_ext}"
                    
                    cursor.execute('''
                        INSERT INTO vod_streams VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (stream.get('stream_id'), stream.get('name'),
                         stream.get('category_id'), stream_url,
                         stream.get('year'), stream.get('rating'), stream.get('genre')))
    
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
        
        console.print("\n[dim]Press any key to continue...[/dim]")
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
        console.print("\\nPress Enter to continue, or Escape to cancel...")
        
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
        console.print("\\nPress Enter to continue, or Escape to cancel...")
        
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
            # Check new location first
            if os.path.exists('data/favorites.json'):
                with open('data/favorites.json', 'r') as f:
                    return json.load(f)
            # Fall back to old location for backward compatibility
            elif os.path.exists('favorites.json'):
                with open('favorites.json', 'r') as f:
                    favs = json.load(f)
                # Migrate to new location
                os.makedirs('data', exist_ok=True)
                with open('data/favorites.json', 'w') as f:
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
            os.makedirs('data', exist_ok=True)
            
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
            with open('data/favorites.json', 'w') as f:
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
            os.makedirs('data', exist_ok=True)
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
            
            with open('data/iptv.m3u', 'w', encoding='utf-8') as f:
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
                with open('data/favorites.json', 'w') as f:
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
                conn = sqlite3.connect('iptv.db')
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

def main():
    """Main entry point"""
    try:
        # Check if MPV is available
        subprocess.run(['mpv', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("Warning: MPV not found. Install it to play streams.")
        console.print("Ubuntu/Debian: sudo apt install mpv")
        console.print("macOS: brew install mpv")
        console.print("\nPress Enter to continue...")
        input()
    
    manager = IPTVMenuManager()
    manager.main_menu()

if __name__ == "__main__":
    main()