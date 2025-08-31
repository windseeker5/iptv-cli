#!/usr/bin/env python3
"""
Menu-Driven IPTV CLI with Arrow Key Navigation
Similar to Chris Titus linutil interface style

Install: pip install -r requirements.txt
Run: python3 menu_driven_iptv.py
"""

import os
import sys
import sqlite3
import requests
import json
import subprocess
from datetime import datetime
from simple_term_menu import TerminalMenu
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

console = Console()

class IPTVMenuManager:
    def __init__(self):
        self.db_path = "iptv.db"
        self.server = "http://cf.its-cdn.me"
        self.username = "1498fb9676b4"
        self.password = "d808eed40f"
        self.inject_server = None  # Set your inject server URL here
        
    def wait_for_escape(self):
        """Wait for escape key instead of enter"""
        import termios, sys, tty
        try:
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            tty.cbreak(fd)
            console.print("\nPress [dim white]Escape[/dim white] to continue...")
            while True:
                char = sys.stdin.read(1)
                if ord(char) == 27:  # ESC key
                    break
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except:
            # Fallback for environments where termios doesn't work
            self.wait_for_escape()
        
    def main_menu(self):
        """Main menu with arrow key navigation"""
        while True:
            console.clear()
            console.print("[orange]✻[/orange] Welcome to IPTV cli !")
            console.print()
            
            # Show database status
            self.show_status()
            
            options = [
                "Download/Update Database",
                "Search",
                "Browse Categories",
                "Database Statistics",
                "Exit"
            ]
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> ",
                cycle_cursor=True,
                clear_screen=False
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == 4:  # Exit
                console.print("\nGoodbye!")
                break
            elif choice == 0:  # Download/Update
                self.download_menu()
            elif choice == 1:  # Search
                self.unified_search_menu()
            elif choice == 2:  # Browse Categories
                self.browse_categories_menu()
            elif choice == 3:  # Statistics
                self.show_statistics()
    
    def show_status(self):
        """Show current database status"""
        if os.path.exists(self.db_path):
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                live_count = cursor.execute("SELECT COUNT(*) FROM live_streams").fetchone()[0]
                vod_count = cursor.execute("SELECT COUNT(*) FROM vod_streams").fetchone()[0]
                conn.close()
                
                status = f"[dim white]●[/dim white] Database: Ready | Live: {live_count:,} | VOD: {vod_count:,}"
                console.print(Panel(status, style="dim white"))
            except:
                console.print(Panel("⚫ Database: Error reading", style="red"))
        else:
            console.print(Panel("⚫ Database: Not found - Use 'Download/Update Database'", style="yellow"))
    
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
            
            options = []
            all_results = []
            
            # Add live channel results with [LIVE] prefix
            for result in live_results:
                category = result['category_name'] or 'Unknown'
                option = f"[LIVE] {result['name'][:45]} | {category[:12]} | ID: {result['stream_id']}"
                options.append(option)
                all_results.append(('live', result))
            
            # Add VOD results with [VOD] prefix
            for result in vod_results:
                year = result['year'] or 'N/A'
                rating = f"{result['rating']:.1f}" if result['rating'] else 'N/A'
                genre = result['genre'][:12] if result['genre'] else 'Unknown'
                option = f"[VOD] {result['name'][:37]} ({year}) | {rating} | {genre}"
                options.append(option)
                all_results.append(('vod', result))
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == len(all_results):  # Back
                break
            
            if 0 <= choice < len(all_results):
                result_type, selected = all_results[choice]
                if result_type == 'live':
                    self.channel_action_menu(selected)
                else:  # VOD
                    self.play_with_mpv({'name': selected['name'], 'stream_url': selected['stream_url']})
    
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
            SELECT name, category_name, stream_id, stream_url
            FROM live_streams 
            WHERE name LIKE ? 
            ORDER BY name 
            LIMIT 50
        """
        
        results = cursor.execute(sql, (f'%{query}%',)).fetchall()
        conn.close()
        
        return [dict(zip(['name', 'category_name', 'stream_id', 'stream_url'], row)) for row in results]
    
    def show_live_results(self, results, search_term):
        """Show live channel results with arrow navigation"""
        while True:
            console.clear()
            console.print(Panel.fit(f"Live Channels: '{search_term}' ({len(results)} found)", style="dim white"))
            
            # Create menu options from results
            options = []
            for i, result in enumerate(results):
                category = result['category_name'] or 'Unknown'
                option = f"{result['name'][:50]} | {category[:15]} | ID: {result['stream_id']}"
                options.append(option)
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == len(results):  # Back
                break
            
            if 0 <= choice < len(results):
                selected = results[choice]
                self.channel_action_menu(selected)
    
    def channel_action_menu(self, channel):
        """Menu for channel actions"""
        console.clear()
        console.print(Panel.fit(f"Channel: {channel['name']}", style="dim white"))
        console.print(f"Category: {channel['category_name'] or 'Unknown'}")
        console.print(f"Stream ID: {channel['stream_id']}")
        console.print()
        
        options = [
            "Play with MPV",
            "Stream to Inject Server", 
            "Copy Stream URL",
            "Show Channel Details",
            "Back to Results"
        ]
        
        terminal_menu = TerminalMenu(
            options,
            title="",
            menu_cursor="> "
        )
        
        choice = terminal_menu.show()
        
        if choice == 0:  # Play MPV
            self.play_with_mpv(channel)
        elif choice == 1:  # Inject server
            self.stream_to_inject_server(channel)
        elif choice == 2:  # Copy URL
            self.copy_stream_url(channel)
        elif choice == 3:  # Details
            self.show_channel_details(channel)
    
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
            SELECT name, year, rating, genre, stream_url
            FROM vod_streams 
            WHERE name LIKE ? 
            ORDER BY name 
            LIMIT 50
        """
        
        results = cursor.execute(sql, (f'%{query}%',)).fetchall()
        conn.close()
        
        return [dict(zip(['name', 'year', 'rating', 'genre', 'stream_url'], row)) for row in results]
    
    def show_vod_results(self, results, search_term):
        """Show VOD results with arrow navigation"""
        while True:
            console.clear()
            console.print(Panel.fit(f"VOD Content: '{search_term}' ({len(results)} found)", style="dim white"))
            
            # Create menu options from results
            options = []
            for result in results:
                year = result['year'] or 'N/A'
                rating = f"{result['rating']:.1f}" if result['rating'] else 'N/A'
                genre = result['genre'][:15] if result['genre'] else 'Unknown'
                option = f"{result['name'][:40]} ({year}) | {rating} | {genre}"
                options.append(option)
            
            options.append("Back to Search")
            
            terminal_menu = TerminalMenu(
                options,
                title="",
                menu_cursor="> "
            )
            
            choice = terminal_menu.show()
            
            if choice is None or choice == len(results):  # Back
                break
            
            if 0 <= choice < len(results):
                selected = results[choice]
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
            SELECT name, stream_id, stream_url, category_name
            FROM live_streams 
            WHERE category_name = ?
            ORDER BY name
            LIMIT 100
        """
        
        results = cursor.execute(sql, (category_name,)).fetchall()
        conn.close()
        
        if not results:
            return
        
        channels = [dict(zip(['name', 'stream_id', 'stream_url', 'category_name'], row)) for row in results]
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
        try:
            subprocess.run(['mpv', '--version'], capture_output=True, check=True)
            
            console.print(f"\nStarting MPV: {channel['name']}")
            subprocess.Popen([
                'mpv',
                '--title', f"IPTV: {channel['name']}",
                '--really-quiet',
                channel['stream_url']
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            console.print("MPV started successfully")
            self.wait_for_escape()
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            console.print("\nMPV not found. Install MPV to play streams.")
            console.print(f"Stream URL: {channel['stream_url']}")
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
    
    def show_channel_details(self, channel):
        """Show detailed channel information"""
        console.print(f"\nChannel Details:")
        console.print(f"Name: {channel['name']}")
        console.print(f"Category: {channel['category_name'] or 'Unknown'}")
        console.print(f"Stream ID: {channel['stream_id']}")
        console.print(f"Stream URL: {channel['stream_url']}")
        self.wait_for_escape()
    
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
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                with open("account_info.json", "w") as f:
                    json.dump(response.json(), f, indent=2)
                return True
        except:
            pass
        return False
    
    def _download_live_categories(self):
        """Download live categories"""
        try:
            url = f"{self.server}/player_api.php?username={self.username}&password={self.password}&action=get_live_categories"
            response = requests.get(url, timeout=30)
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
            response = requests.get(url, timeout=120)
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
            response = requests.get(url, timeout=30)
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
            response = requests.get(url, timeout=120)
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
            response = requests.get(url, timeout=30)
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
                    category_name TEXT
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
                        INSERT INTO live_streams VALUES (?, ?, ?, ?, ?)
                    ''', (stream.get('stream_id'), stream.get('name'),
                         stream.get('category_id'), stream_url, cat_name))
        
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

def main():
    """Main entry point"""
    try:
        # Check if MPV is available
        subprocess.run(['mpv', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("Warning: MPV not found. Install it to play streams.")
        console.print("Ubuntu/Debian: sudo apt install mpv")
        console.print("macOS: brew install mpv")
        self.wait_for_escape()
    
    manager = IPTVMenuManager()
    manager.main_menu()

if __name__ == "__main__":
    main()