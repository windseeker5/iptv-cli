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
            console.print("[dim white]Press ESC to continue...[/dim white]")
            self.wait_for_escape()
        else:
            console.print()
            console.print("[red]✗ Database update failed![/red]")
            console.print("You can try updating manually from the menu.")
            console.print("[dim white]Press ESC to continue...[/dim white]")
            self.wait_for_escape()
        
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
            
            # Show database status
            self.show_status()
            
            options = [
                "Search IPTV",
                "Discovery Hub",
                "Update IPTV db",
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
            
            if choice is None:  # ESC pressed
                console.print("\nGoodbye!")
                break
            elif choice == 0:  # Search IPTV
                self.unified_search_menu()
            elif choice == 1:  # Discovery Hub
                self.browse_categories_menu()
            elif choice == 2:  # Update IPTV db
                self.download_menu()
            elif choice == 3:  # Streaming Infrastructure
                self.streaming_infrastructure_menu()
    
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
                
                # Join all lines and display in panel
                status = "\n".join(status_lines)
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
            console.print("[dim white]# (s)ave | (d)elete | (i)nfo | (r)estream | (c)download | (p)lay[/dim white]\n")
            
            options = []
            all_results = []
            
            # Get current favorites for checking
            favorites_set = self.get_favorites_set()
            
            # Add live channel results with [LIVE] prefix and favorite indicator
            # Keep options shorter to prevent truncation in terminal menu
            for result in live_results:
                is_fav = (result.get('stream_id'), 'live') in favorites_set
                fav_indicator = "⭐ " if is_fav else "   "
                # Shorter format without category and ID to prevent truncation
                option = f"{fav_indicator}[LIVE] {result['name']}"
                options.append(option)
                all_results.append(('live', result))
            
            # Add VOD results with [VOD] prefix and favorite indicator
            for result in vod_results:
                # Extract year from name if not in year field
                year_match = re.search(r'\((\d{4})\)', result['name'])
                if year_match:
                    year = year_match.group(1)
                    # Remove year from display name
                    display_name = re.sub(r'\s*\(\d{4}\)\s*', '', result['name'])
                else:
                    year = result.get('year') or 'N/A'
                    display_name = result['name']
                    
                rating = f"{result['rating']:.1f}" if result['rating'] else 'N/A'
                
                # Use category_name as genre if available
                if result.get('category_name'):
                    # Simplify category for display
                    category = result['category_name']
                    if ' - ' in category:
                        parts = category.split(' - ', 1)
                        genre = parts[0][:3] if len(parts) > 0 else 'VOD'
                    else:
                        genre = category[:8]
                else:
                    genre = result.get('genre', 'VOD')[:8] if result.get('genre') else 'VOD'
                    result['category_name'] = f"VOD/{genre}"
                    
                is_fav = (result.get('stream_id'), 'vod') in favorites_set
                fav_indicator = "⭐ " if is_fav else "   "
                # Compact format for unified search
                option = f"{fav_indicator}[VOD] {rating} {year:<4} {display_name[:20]}"
                options.append(option)
                all_results.append(('vod', result))
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "s", "d", "p", "r", "c", "i"),
                show_shortcut_hints=False
            )
            
            choice = terminal_menu.show()
            chosen_key = terminal_menu.chosen_accept_key
            
            if choice is None or choice == len(all_results):  # Back
                break
            
            if 0 <= choice < len(all_results):
                result_type, selected = all_results[choice]
                
                # Handle shortcuts
                if chosen_key == 'p':  # Play directly
                    self.play_with_mpv(selected)
                    continue  # Stay in menu after playing
                    
                elif chosen_key == 'i':  # Show information screen
                    if result_type == 'live':
                        self.show_live_stream_info(selected)
                    else:  # VOD
                        self.show_vod_info(selected)
                    continue  # Return to menu after viewing info
                    
                elif chosen_key == 'r':  # Restream directly
                    self.restream_placeholder(selected)
                    continue  # Stay in menu after restreaming
                    
                elif chosen_key == 'c':  # Download content
                    if result_type == 'vod':
                        self.download_vod_to_data(selected)
                    else:
                        self.download_live_to_data(selected)
                    continue  # Stay in menu after downloading
                    
                elif chosen_key == 's':  # Save to favorites
                    result = self.save_to_favorites(selected, result_type)
                    if result == -1:
                        console.print("[yellow]⚠[/yellow] Already in favorites!")
                        self.wait_for_escape()
                    elif result > 0:
                        console.print(f"[green]✓[/green] Added to favorites ({result} total)")
                        self.wait_for_escape()
                    else:
                        console.print("[red]✗[/red] Failed to add to favorites")
                        self.wait_for_escape()
                    continue  # Refresh menu immediately
                    
                elif chosen_key == 'd':  # Delete from favorites
                    result = self.remove_from_favorites(selected, result_type)
                    if result > 0:
                        console.print(f"[green]✓[/green] Removed from favorites ({result} remaining)")
                        self.wait_for_escape()
                    else:
                        console.print("[yellow]⚠[/yellow] Not in favorites")
                        self.wait_for_escape()
                    continue  # Refresh menu immediately
                    
                else:  # Enter key - show action menu (fallback for backwards compatibility)
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
        """Show live channel results with arrow navigation, keyboard shortcuts and pagination"""
        page_size = 25
        current_page = 0
        total_pages = (len(results) + page_size - 1) // page_size if results else 1
        
        while True:
            console.clear()
            
            # Display header with page info
            if total_pages > 1:
                console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found) - Page {current_page + 1}/{total_pages}", style="dim white"))
            else:
                console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found)", style="dim white"))
            
            console.print("[dim white]# (s)ave | (d)elete | (i)nfo | (r)estream | (c)download | (p)lay[/dim white]\n")
            
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
                category = result['category_name'] or 'Unknown'
                is_fav = (result.get('stream_id'), 'live') in favorites_set
                fav_indicator = "⭐ " if is_fav else "   "
                option = f"{fav_indicator}{result['name'][:48]} | {category[:15]} | ID: {result['stream_id']}"
                options.append(option)
            
            # Add Next Page option if not on last page
            if current_page < total_pages - 1:
                options.append("Next Page →")
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                accept_keys=("enter", "p", "i", "r", "c", "s", "d"),
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
                    self.restream_placeholder(selected)
                    continue  # Stay in menu after restreaming
                    
                elif chosen_key == 'c':  # Download (changed from Copy URL)
                    self.download_live_to_data(selected)
                    continue  # Stay in menu after downloading
                    
                elif chosen_key == 's':  # Save to favorites
                    result = self.save_to_favorites(selected, 'live')
                    if result == -1:
                        console.print("[yellow]⚠[/yellow] Already in favorites!")
                    elif result > 0:
                        console.print(f"[green]✓[/green] Added to favorites ({result} total)")
                    else:
                        console.print("[red]✗[/red] Failed to add to favorites")
                    console.print("Press any key to continue...")
                    input()
                    continue  # Refresh menu immediately
                    
                elif chosen_key == 'd':  # Delete from favorites
                    result = self.remove_from_favorites(selected, 'live')
                    if result:
                        console.print(f"[green]✓[/green] Removed from favorites")
                    else:
                        console.print("[yellow]⚠[/yellow] Not in favorites")
                    console.print("Press any key to continue...")
                    input()
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
            
            console.print("[dim white]# (s)ave | (d)elete | (i)nfo | (r)estream | (c)download | (p)lay[/dim white]\n")
            
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
                if result['rating']:
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
                
                # Format: score year title (no star, compact rating)
                if year != 'N/A':
                    option = f"{fav_indicator}{rating:>3} {year} {clean_name[:25]}"
                else:
                    option = f"{fav_indicator}{rating:>3} {clean_name[:30]}"
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
                if chosen_key == 's':  # Save to favorites
                    result = self.save_to_favorites(selected, 'vod')
                    if result == -1:
                        console.print("[yellow]⚠[/yellow] Already in favorites!")
                        self.wait_for_escape()
                    elif result > 0:
                        console.print(f"[green]✓[/green] Added to favorites ({result} total)")
                        self.wait_for_escape()
                    continue
                    
                elif chosen_key == 'd':  # Delete from favorites
                    result = self.remove_from_favorites(selected, 'vod')
                    if result > 0:
                        console.print(f"[green]✓[/green] Removed from favorites ({result} remaining)")
                        self.wait_for_escape()
                    else:
                        console.print("[yellow]⚠[/yellow] Not in favorites")
                        self.wait_for_escape()
                    continue
                    
                elif chosen_key == 'i':  # Show info
                    self.show_vod_info(selected)
                    continue
                    
                elif chosen_key == 'r':  # Restream
                    self.restream_placeholder(selected)
                    continue
                    
                elif chosen_key == 'c':  # Download
                    self.download_vod_to_data(selected)
                    continue
                    
                elif chosen_key == 'p':  # Play
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
                    continue
                    
                else:  # Enter key - show action menu or play
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
    
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
                f"Inject Server URL: {self.inject_server or 'Not set'}",
                "Test MPV Installation",
                "Database Information"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None:  # ESC
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
    
    
    def download_live_to_data(self, live_item):
        """Download/Record live stream to data folder"""
        console.clear()
        console.print(Panel.fit(f"Download Live: {live_item['name']}", style="dim white"))
        
        # Create data folder if it doesn't exist
        import os
        data_folder = "data"
        if not os.path.exists(data_folder):
            os.makedirs(data_folder)
            console.print(f"[green]✓[/green] Created {data_folder} folder")
        
        # Generate filename with timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = "".join(c for c in live_item['name'] if c.isalnum() or c in (' ', '-', '_')).rstrip()
        filename = f"{safe_filename}_{timestamp}.ts".replace("  ", " ")
        filepath = os.path.join(data_folder, filename)
        
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
            pid_file = os.path.join(self.data_dir, f".restream_{stream_key}.pid")
            with open(pid_file, "w") as f:
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
            ORDER BY vs.name
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
            console.print(Panel.fit("Streaming Infrastructure\n[dim white]Dashboard: http://localhost:8080[/dim white]", style="bright_cyan"))
            
            # Check installation status
            docker_installed = self.is_docker_installed()
            lazydocker_installed = self.is_lazydocker_installed()
            compose_exists = os.path.exists('docker-compose.yml')
            
            # Build menu options with status indicators
            options = []
            
            # Installation options with status
            if docker_installed:
                options.append("Install Docker [Installed]")
            else:
                options.append("Install Docker [Required]")
                
            if lazydocker_installed:
                options.append("Install Lazydocker [Installed]")
            else:
                options.append("Install Lazydocker")
            
            # Only show these if Docker is installed
            if docker_installed:
                if lazydocker_installed:
                    options.append("Launch Lazydocker")
                
                if compose_exists:
                    options.append("Review/Edit docker-compose.yml")
                else:
                    options.append("Create docker-compose.yml")
                    
                options.append("Start All Services")
                options.append("Stop All Services")
                options.append("Restart All Services")
                
                # Get running container count for inline status
                running_count = self.get_running_container_count()
                total_count = 3  # NGINX-RTMP, Jellyfin, Samba
                options.append(f"Container Status & URLs [{running_count}/{total_count} Running]")
                
                options.append("View Logs (last 50 lines)")
                options.append("Update Container Images")
            
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
            if "Install Docker" in selected_option and not docker_installed:
                self.install_docker()
            elif "Install Lazydocker" in selected_option and not lazydocker_installed:
                self.install_lazydocker()
            elif "Launch Lazydocker" in selected_option:
                self.launch_lazydocker()
            elif "Review/Edit docker-compose.yml" in selected_option or "Create docker-compose.yml" in selected_option:
                self.edit_docker_compose()
            elif "Start All Services" in selected_option:
                self.start_all_services()
            elif "Stop All Services" in selected_option:
                self.stop_all_services()
            elif "Restart All Services" in selected_option:
                self.restart_all_services()
            elif "Container Status & URLs" in selected_option:
                self.show_container_status_and_urls()
            elif "View Logs" in selected_option:
                self.view_container_logs()
            elif "Update Container Images" in selected_option:
                self.update_container_images()
    
    def container_management_menu(self):
        """Legacy redirect to new menu"""
        self.streaming_infrastructure_menu()
    
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
                console.print("[green]✓[/green] docker-compose.yml is valid")
                console.print()
                console.print("Would you like to restart services now? (y/n): ", end="")
                
                try:
                    response = input().strip().lower()
                    if response == 'y':
                        self.restart_all_services()
                    else:
                        console.print("Services not restarted. You can restart them manually from the menu.")
                except KeyboardInterrupt:
                    pass
            else:
                console.print("[red]✗[/red] docker-compose.yml has errors:")
                console.print(result.stderr.decode())
                
        except FileNotFoundError:
            console.print(f"[red]✗[/red] Editor '{editor}' not found. Install nano or set EDITOR environment variable.")
        except Exception as e:
            console.print(f"[red]✗[/red] Error editing file: {e}")
        
        self.wait_for_escape()
    
    def start_all_services(self):
        """Start all Docker services using docker-compose"""
        console.clear()
        console.print(Panel.fit("Starting All Services", style="dim white"))
        
        if not os.path.exists('docker-compose.yml'):
            console.print("[red]✗[/red] docker-compose.yml not found")
            console.print("Please create or review the docker-compose.yml first")
            self.wait_for_escape()
            return
        
        console.print("Starting all services...")
        
        try:
            result = subprocess.run(['docker-compose', 'up', '-d'], 
                                  capture_output=True, check=True, timeout=120)
            
            console.print("[green]✓[/green] All services started successfully!")
            console.print()
            console.print("Output:")
            console.print(result.stdout.decode())
            
            if result.stderr.decode():
                console.print(f"[dim]Stderr:[/dim] {result.stderr.decode()}")
                
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to start services: {e}")
            if e.stdout:
                console.print(f"Output: {e.stdout.decode()}")
            if e.stderr:
                console.print(f"Error: {e.stderr.decode()}")
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found. Please install Docker Compose.")
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] Operation timed out. Services may still be starting.")
        
        self.wait_for_escape()
    
    def stop_all_services(self):
        """Stop all Docker services using docker-compose"""
        console.clear()
        console.print(Panel.fit("Stopping All Services", style="dim white"))
        
        console.print("Stopping all services...")
        
        try:
            result = subprocess.run(['docker-compose', 'down'], 
                                  capture_output=True, check=True, timeout=60)
            
            console.print("[green]✓[/green] All services stopped successfully!")
            console.print()
            console.print("Output:")
            console.print(result.stdout.decode())
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to stop services: {e}")
            if e.stdout:
                console.print(f"Output: {e.stdout.decode()}")
            if e.stderr:
                console.print(f"Error: {e.stderr.decode()}")
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found")
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] Operation timed out")
        
        self.wait_for_escape()
    
    def restart_all_services(self):
        """Restart all Docker services"""
        console.clear()
        console.print(Panel.fit("Restarting All Services", style="dim white"))
        
        console.print("Restarting all services...")
        console.print("This will stop and start all containers...")
        console.print()
        
        try:
            # Stop services
            console.print("Stopping services...")
            result = subprocess.run(['docker-compose', 'down'], 
                                  capture_output=True, check=True, timeout=60)
            console.print("[green]✓[/green] Services stopped")
            
            # Start services
            console.print("Starting services...")
            result = subprocess.run(['docker-compose', 'up', '-d'], 
                                  capture_output=True, check=True, timeout=120)
            
            console.print("[green]✓[/green] All services restarted successfully!")
            console.print()
            console.print("Output:")
            console.print(result.stdout.decode())
            
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to restart services: {e}")
            if e.stdout:
                console.print(f"Output: {e.stdout.decode()}")
            if e.stderr:
                console.print(f"Error: {e.stderr.decode()}")
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found")
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] Operation timed out")
        
        self.wait_for_escape()
    
    def view_container_logs(self):
        """View last 50 lines of container logs"""
        console.clear()
        console.print(Panel.fit("Container Logs", style="dim white"))
        
        try:
            console.print("Fetching logs from all containers...")
            console.print()
            
            # Get logs from docker-compose
            result = subprocess.run(['docker-compose', 'logs', '--tail=50'], 
                                  capture_output=True, check=True, timeout=10)
            
            output = result.stdout.decode()
            if output:
                console.print(output)
            else:
                console.print("[dim]No logs available[/dim]")
                
            if result.stderr.decode():
                console.print(f"[dim]Stderr:[/dim] {result.stderr.decode()}")
                
        except subprocess.CalledProcessError as e:
            console.print(f"[red]✗[/red] Failed to fetch logs: {e}")
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found")
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] Operation timed out")
        
        self.wait_for_escape()
    
    def update_container_images(self):
        """Update/pull latest container images"""
        console.clear()
        console.print(Panel.fit("Update Container Images", style="dim white"))
        
        console.print("Pulling latest container images...")
        console.print("This may take several minutes depending on your connection...")
        console.print()
        
        try:
            # Pull latest images
            result = subprocess.run(['docker-compose', 'pull'], 
                                  capture_output=True, check=False, timeout=300)
            
            console.print("Output:")
            console.print(result.stdout.decode())
            
            if result.returncode == 0:
                console.print()
                console.print("[green]✓[/green] Images updated successfully!")
                console.print()
                console.print("Would you like to restart services with new images? (y/n): ", end="")
                
                try:
                    response = input().strip().lower()
                    if response == 'y':
                        self.restart_all_services()
                except KeyboardInterrupt:
                    pass
            else:
                console.print("[yellow]⚠[/yellow] Some images may have failed to update")
                if result.stderr.decode():
                    console.print(f"Error: {result.stderr.decode()}")
                    
        except FileNotFoundError:
            console.print("[red]✗[/red] docker-compose not found")
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠[/yellow] Operation timed out. Pull may still be running.")
        except Exception as e:
            console.print(f"[red]✗[/red] Error updating images: {e}")
        
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
            
            # Load data from JSON files
            self._load_data_from_json(cursor)
            
            # Create indexes
            cursor.execute("CREATE INDEX idx_live_name ON live_streams(name)")
            cursor.execute("CREATE INDEX idx_vod_name ON vod_streams(name)")
            cursor.execute("CREATE INDEX idx_vod_cat_name ON vod_categories(category_name)")
            
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