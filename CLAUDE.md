# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a menu-driven IPTV CLI application that provides arrow key navigation to browse, search, and play IPTV content. The application uses a SQLite database to store downloaded IPTV data and integrates with external players like MPV.

## Commands

### Setup and Installation
```bash
# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env with your IPTV server credentials

# Install MPV player (required for streaming)
# Ubuntu/Debian:
sudo apt install mpv
# macOS:
brew install mpv
```

### Running the Application
```bash
# Run the main application
python3 iptv.py
```

### Development Commands
```bash
# Check Python syntax
python3 -m py_compile iptv.py

# Run the main application
python3 iptv.py

# Run alternative Textual UI implementation (experimental)
python3 iptv_textual.py
# or use the shell script with proper terminal handling:
./run_textual.sh

# Run comprehensive test suite
python3 test/run_all_tests.py

# Run individual test files for specific functionality
python3 test/test_api_connection.py        # Test server connection
python3 test/test_full_workflow.py         # Complete workflow validation
python3 test/test_ui_simulation.py         # UI functionality tests
python3 test/test_menu_simulation.py       # Menu system tests
python3 test/test_database_creation.py     # Database creation tests
python3 test/test_exact_menu_flow.py       # Menu flow validation
python3 test/test_status_display.py        # Status display tests
python3 test/test_improved_downloads.py    # Download functionality tests

# Quick syntax check for development
timeout 3 python3 iptv.py                 # Quick validation without full startup
```

## Architecture

### Core Components

- **IPTVMenuManager**: Main application class that handles all menu navigation and core functionality
- **Database Layer**: SQLite database (`iptv.db`) with tables for live streams, VOD content, categories, and account info
- **API Integration**: Downloads IPTV data from configured server using REST API endpoints
- **Media Player Integration**: Launches external MPV player for stream playback

### Key Features

1. **Menu System**: Uses `simple-term-menu` for arrow key navigation throughout the application
2. **Data Download**: Fetches live streams, VOD content, and categories from IPTV API server
3. **Search Functionality**: Full-text search across live channels and VOD content
4. **Category Browsing**: Hierarchical browsing of content by categories
5. **Stream Playback**: Integration with MPV player for media playback
6. **Database Management**: Automatic SQLite database creation and updates

### Database Schema

- `live_streams`: Live TV channels with stream URLs, categories, and metadata
- `vod_streams`: Video-on-demand content with ratings, genres, and years
- `account_info`: User account status and connection limits

### Configuration

The application uses environment variables for IPTV server credentials:
- Copy `.env.example` to `.env` and configure your credentials
- Required: `IPTV_SERVER_URL`, `IPTV_USERNAME`, `IPTV_PASSWORD`
- Optional: `INJECT_SERVER_URL` for streaming injection
- The `.env` file is excluded from git to protect sensitive credentials

### File Structure

- `iptv.py`: Main application file (~879 lines) containing all functionality
- `iptv_textual.py`: Alternative implementation using Textual framework (experimental)
- `requirements.txt`: Python package dependencies
- `iptv.db`: SQLite database (created after first data download)
- `test/`: Comprehensive test suite with multiple test files
- `data/`: JSON files created during API data downloads
- `wireframe.md`: Design specifications for the terminal interface

### Implementation Variants

The codebase includes two UI implementations:

1. **iptv.py** (Primary): Simple terminal interface using `simple-term-menu` for arrow key navigation
2. **iptv_textual.py** (Experimental): Rich TUI using the Textual framework with cards and advanced layouts

Both implementations share the same core database and API logic but provide different user experiences.

### Testing Architecture

- `test/run_all_tests.py`: Master test runner that executes all tests in sequence
- Individual test files focus on specific functionality:
  - API connection validation
  - Database operations
  - Menu system simulation  
  - Complete workflow testing
  - UI functionality validation
- Tests use subprocess calls to validate actual application behavior
- Test suite provides comprehensive validation of all core features

## Development Notes

- The application is designed as a single-file Python script (~879 lines) for simplicity
- Uses Rich library for enhanced terminal formatting and progress bars
- All menus use arrow key navigation with the `simple-term-menu` library
- Database operations use raw SQL queries with sqlite3
- External process calls use subprocess for MPV integration and clipboard operations
- Test-driven development approach with comprehensive test coverage
- Environment variable validation prevents runtime configuration errors