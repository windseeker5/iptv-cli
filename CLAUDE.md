# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a menu-driven IPTV CLI application that provides arrow key navigation to browse, search, and play IPTV content. The application uses a SQLite database to store downloaded IPTV data and integrates with external players like MPV. It includes a comprehensive NGINX-RTMP restreaming feature with Docker containers for sharing streams.

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

# Install Docker and Docker Compose (for NGINX-RTMP feature)
sudo apt install docker.io docker-compose  # Ubuntu/Debian
brew install docker docker-compose         # macOS

# Install FFmpeg (required for restreaming)
sudo apt install ffmpeg                    # Ubuntu/Debian
brew install ffmpeg                        # macOS
```

### Running the Application
```bash
# Run the main application
python3 iptv.py
```

### Docker/NGINX-RTMP Commands
```bash
# Build and start NGINX-RTMP containers
docker-compose up -d --build

# Stop containers
docker-compose down

# View container logs
docker logs iptv-nginx-rtmp

# Rebuild containers after configuration changes
docker-compose build --no-cache && docker-compose up -d
```

## Architecture

### Core Components

- **IPTVMenuManager**: Main application class (~1700 lines) that handles all menu navigation and core functionality
- **Database Layer**: SQLite database (`iptv.db`) with tables for live streams, VOD content, categories, and account info
- **API Integration**: Downloads IPTV data from configured server using REST API endpoints
- **Media Player Integration**: Launches external MPV player for stream playback
- **NGINX-RTMP Server**: Docker-based restreaming server with HLS output and web interface

### Key Features

1. **Menu System**: Uses `simple-term-menu` for arrow key navigation throughout the application
2. **Data Download**: Fetches live streams, VOD content, and categories from IPTV API server
3. **Search Functionality**: Full-text search across live channels and VOD content with unified results
4. **Category Browsing**: Hierarchical browsing of content by categories
5. **Stream Playback**: Integration with MPV player for media playback
6. **Database Management**: Automatic SQLite database creation and updates with indexing
7. **Restreaming**: FFmpeg-based restreaming through NGINX-RTMP with multiple quality outputs
8. **Web Interface**: Beautiful dark-themed live streaming page with HLS.js player and Chromecast support

### Database Schema

- `live_streams`: Live TV channels with stream URLs, categories, and metadata
- `vod_streams`: Video-on-demand content with ratings, genres, and years  
- `account_info`: User account status and connection limits

### Configuration

The application uses environment variables for IPTV server credentials to ensure security:
- **Required Setup**: Copy `.env.example` to `.env` and configure your credentials
- **Required Variables**: `IPTV_SERVER_URL`, `IPTV_USERNAME`, `IPTV_PASSWORD`
- **Optional Variables**: `INJECT_SERVER_URL` for streaming injection, NGINX port configurations
- **Security**: The `.env` file is excluded from git to protect sensitive credentials
- **Validation**: The application validates all required environment variables on startup

### File Structure

- `iptv.py`: Main application file (~1700 lines) containing all functionality
- `requirements.txt`: Python package dependencies
- `iptv.db`: SQLite database (created after first data download)
- `test/`: Comprehensive test suite with multiple test files and master runner
- `docker-compose.yml`: Multi-service Docker configuration for NGINX-RTMP
- `nginx/`: NGINX-RTMP server configuration and web interface files
- `data/`: JSON files created during API data downloads
- `.env`: Environment variables (excluded from git)

### NGINX-RTMP Restreaming Architecture

The restreaming feature creates a complete media server:

1. **Docker Infrastructure**: Multi-service setup with NGINX-RTMP container
2. **Stream Processing**: FFmpeg handles transcoding and pushing to RTMP server
3. **Multiple Outputs**: Automatic generation of source, medium, and low quality variants
4. **Web Interface**: Professional streaming page with HLS.js player and Chromecast support
5. **Live Streaming Page**: Dark-themed player at `/live` with auto-stream detection

### Network Flow
```
IPTV Source → FFmpeg → NGINX-RTMP → HLS/RTMP Output → Web Player/Chromecast
```

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

- The application is designed as a single-file Python script for simplicity while maintaining comprehensive functionality
- Uses Rich library for enhanced terminal formatting and progress bars
- All menus use arrow key navigation with the `simple-term-menu` library
- Database operations use raw SQL queries with sqlite3 for performance
- External process calls use subprocess for MPV integration, Docker management, and FFmpeg operations
- Test-driven development approach with comprehensive test coverage
- Environment variable validation prevents runtime configuration errors
- Docker integration provides professional-grade restreaming capabilities
- The live streaming web interface supports modern browsers and casting devices

## Important Notes

- Always use environment variables for credentials - never hardcode sensitive information
- The application requires internet connectivity for IPTV API access and stream playback
- Docker and FFmpeg are optional but required for restreaming functionality
- MPV is required for local stream playback
- The comprehensive test suite should be run before making significant changes