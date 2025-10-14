# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a comprehensive IPTV management and streaming infrastructure built in Python with Docker services. The system provides a terminal-based interface for managing IPTV content, restreaming capabilities, and media server integration.

## Core Architecture

### Main Components

**iptv.py** - The central application featuring:
- Menu-driven CLI interface using `simple-term-menu` for arrow key navigation
- `IPTVMenuManager` class handling all core functionality
- SQLite database operations for content management
- IPTV API integration for live streams and VOD content
- YouTube Tool for searching, playing, and downloading YouTube videos
- Favorites system with M3U playlist generation
- Container orchestration for streaming infrastructure

**Docker Infrastructure** (docker-compose.yml):
- **nginx-rtmp**: NGINX-RTMP server for live restreaming (port 1935 RTMP, 8080 HTTP, 8081 admin)
- **jellyfin**: Media server for library management (port 8096)
- **samba**: Network file sharing for TV device access (ports 137-139, 445)

**Data Organization**:
- `data/` folder: All application data (database, JSON files, favorites, playlists)
- `data/youtube/` folder: Downloaded YouTube videos and audio files
- `nginx/html/` folder: Web interface and served content (dual M3U playlist location)
- Auto-migration from old file locations to `data/` folder

### Key Integration Points

1. **Database Management**: Auto-updates every 14 days, stores in `data/iptv.db`
2. **Playlist Generation**: Creates M3U files in both `data/` and `nginx/html/` for serving
3. **Container Communication**: Python app orchestrates Docker services via docker-compose
4. **Stream Processing**: FFmpeg integration for restreaming and transcoding
5. **YouTube Integration**: yt-dlp library for video search, streaming, and downloading

### YouTube Tool Features

The YouTube Tool provides comprehensive YouTube video management:

**Search & Browse**:
- Search YouTube videos with keyword queries
- Display up to 20 results with title, uploader, and duration
- Navigate results with arrow keys

**Video Actions**:
- **Play (P)**: Stream videos directly with MPV player (up to 1080p)
- **Info (I)**: View detailed information including description, views, likes, upload date
- **Download (D)**: Save videos with format options:
  - Best Quality (1080p Video + Audio as MP4)
  - Audio Only (extracted as MP3)
  - 720p Video (optimized quality)

**Technical Details**:
- Uses `yt-dlp` for reliable YouTube access
- Downloads saved to `data/youtube/` directory
- Real-time download progress with speed and ETA
- Automatic format merging for video+audio streams
- FFmpeg required for audio extraction and format conversion

## Development Commands

### Environment Setup
```bash
# Create and activate virtual environment (auto-handled by iptv.py)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your IPTV credentials
```

### Running the Application
```bash
# Main application (handles venv activation automatically)
python3 iptv.py

# Direct utility functions
python3 util.py
```

### Container Management
```bash
# Build and start all services
docker-compose up -d --build

# View logs
docker-compose logs -f [service-name]

# Stop services
docker-compose down

# Rebuild specific service
docker-compose up -d --build nginx-rtmp
```

### Database Operations
```bash
# Database will auto-update on first run or when >14 days old
# Manual database recreation (delete data/iptv.db and restart app)

# Check database status via application menu:
# Main Menu → Update IPTV db → Download Fresh Data
```

## Data Flow Architecture

### IPTV Content Pipeline
1. **API Fetch**: Downloads JSON data from IPTV provider to `data/` folder
2. **Database Creation**: Parses JSON files into SQLite database with proper indexes
3. **Search & Browse**: Terminal interface provides category browsing and search functionality
4. **Favorites Management**: User selections saved to `data/favorites.json`
5. **Playlist Generation**: M3U playlists generated in both `data/` and `nginx/html/` locations

### Streaming Infrastructure
1. **Restreaming**: FFmpeg pulls IPTV streams and pushes to NGINX-RTMP server
2. **HLS Generation**: NGINX creates multiple quality HLS streams automatically
3. **Web Serving**: Static HTML interfaces served from `nginx/html/`
4. **Media Management**: Jellyfin indexes recordings and media from USB mounts

## Environment Configuration

### Required Variables (.env)
- `IPTV_SERVER_URL`: Provider server endpoint
- `IPTV_USERNAME`: Provider username  
- `IPTV_PASSWORD`: Provider password

### Optional Variables
- `USB_*_PATH`: Mount points for USB media (records, movies, music, photos)
- `NGINX_*_PORT`: Custom port mappings for NGINX services
- `JELLYFIN_*_PORT`: Custom port mappings for Jellyfin
- `SAMBA_*_PORT`: Custom port mappings for Samba shares

## Key Design Patterns

### Data Safety
All application data is consolidated in the `data/` folder for safety and organization. The system maintains backward compatibility by migrating old file locations automatically.

### Dual File Strategy
Critical files like M3U playlists are maintained in both `data/` (for backup/organization) and `nginx/html/` (for web serving) to ensure both data safety and service functionality.

### Auto-Environment Management
The application automatically detects and activates Python virtual environments, changing to the correct working directory regardless of execution location.

### Progressive Enhancement
The system gracefully handles missing dependencies, containers, or services, providing clear error messages and fallback options.

## External Dependencies

### System Requirements
- Python 3.7+
- Docker and docker-compose
- FFmpeg (for streaming functionality)
- MPV player (for direct playback)

### Container Images
- Custom NGINX-RTMP build (from nginx/ directory)
- Official Jellyfin media server
- dperson/samba for network sharing

The system is designed for local/development use with security considerations noted for production deployments.