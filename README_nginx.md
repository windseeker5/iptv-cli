# NGINX-RTMP Restreaming Feature

This document describes the new NGINX-RTMP restreaming functionality added to the IPTV CLI application.

## Overview

The NGINX-RTMP feature allows you to:
1. Build and manage a Docker-based NGINX-RTMP server
2. Restream IPTV content through your own server
3. Share restreamed content via HLS and RTMP protocols
4. Provide your own streaming endpoints for redistribution

## Quick Start

### 1. Prerequisites

```bash
# Install Docker and Docker Compose
sudo apt install docker.io docker-compose  # Ubuntu/Debian
# or
brew install docker docker-compose         # macOS

# Install FFmpeg
sudo apt install ffmpeg                    # Ubuntu/Debian
# or  
brew install ffmpeg                        # macOS
```

### 2. Configuration

Update your `.env` file with NGINX ports (optional - defaults provided):

```bash
# NGINX-RTMP Docker Configuration
NGINX_RTMP_PORT=1935
NGINX_HTTP_PORT=8080
NGINX_ADMIN_PORT=8081
```

### 3. Using the Feature

1. Start the IPTV application: `python3 iptv.py`
2. Select **"Build NGINX Container"** from the main menu
3. Choose **"Build & Start NGINX Container"** to create and start the server
4. Navigate to any stream and select **"Restream (Placeholder)"**
5. Choose restreaming options and start the stream

## Features

### Container Management

- **Build & Start NGINX Container**: Creates and starts the NGINX-RTMP server
- **Stop Container**: Gracefully stops the container
- **View Container Logs**: Monitor server logs and debug issues
- **Container Status & URLs**: Shows all available endpoints
- **Test Restream Setup**: Validates the complete setup with a test stream

### Restreaming Options

- **Start Restream**: Direct copy of the stream (best quality)
- **Start with Transcoding**: Transcodes for lower bandwidth (854x480 @ 1Mbps)
- **View Stream URLs**: Shows all sharing URLs and player instructions
- **Stop Active Restream**: Terminates running restreams

### Server Endpoints

Once the container is running, you'll have access to:

- **Web Interface**: `http://localhost:8080`
- **HLS Streams**: `http://localhost:8080/hls/[stream_key].m3u8`
- **RTMP Input**: `rtmp://localhost:1935/live/[stream_key]`
- **Statistics**: `http://localhost:8080/stat`
- **Admin Panel**: `http://localhost:8081`

## Usage Examples

### Restreaming a Live Channel

1. Search for "CNN" in the main menu
2. Select a CNN channel from results
3. Choose "Restream (Placeholder)"
4. Select "Start Restream" for best quality
5. Share the generated HLS URL: `http://localhost:8080/hls/cnn.m3u8`

### Viewing Restreamed Content

- **VLC Player**: Open Network Stream → Paste HLS URL
- **Browser**: Use HLS.js or native HTML5 support
- **OBS Studio**: Add Media Source → Paste HLS URL
- **FFplay**: `ffplay 'http://localhost:8080/hls/stream_key.m3u8'`

### Multiple Quality Streams

The NGINX server automatically creates multiple quality versions:
- **Source**: Original quality (direct copy)
- **Mid**: 854x480 @ 768kbps (automatic)
- **Low**: 480x270 @ 256kbps (automatic)

Access these by appending suffixes to stream keys:
- `http://localhost:8080/hls/cnn_src.m3u8` (source)
- `http://localhost:8080/hls/cnn_mid.m3u8` (medium)
- `http://localhost:8080/hls/cnn_low.m3u8` (low)

## Architecture

### Docker Services

- **nginx-rtmp**: Main NGINX-RTMP server container
- **Future**: Ready for Jellyfin integration

### Directory Structure

```
iptv/
├── docker-compose.yml           # Multi-service configuration
├── nginx/
│   ├── Dockerfile              # NGINX container build
│   ├── nginx.conf              # RTMP server configuration
│   ├── index.html              # Web interface
│   ├── hls/                    # Generated HLS files
│   ├── recordings/             # Stream recordings
│   └── logs/                   # Server logs
└── .restream_*.pid             # Active restream PIDs
```

### Network Flow

```
IPTV Source → FFmpeg → NGINX-RTMP → HLS/RTMP Output
```

1. **Input**: IPTV stream URLs from your provider
2. **Processing**: FFmpeg transcodes and pushes to NGINX-RTMP
3. **Distribution**: NGINX-RTMP serves HLS and RTMP streams
4. **Consumption**: Clients connect via HTTP/RTMP protocols

## Troubleshooting

### Container Won't Start

```bash
# Check Docker status
docker --version
docker-compose --version

# View container logs
docker logs iptv-nginx-rtmp

# Restart containers
docker-compose down && docker-compose up -d --build
```

### FFmpeg Issues

```bash
# Test FFmpeg installation
ffmpeg -version

# Test RTMP connectivity
ffmpeg -re -f lavfi -i testsrc2 -t 10 -f flv rtmp://localhost:1935/live/test
```

### Port Conflicts

If ports 1935 or 8080 are in use, update your `.env` file:

```bash
NGINX_RTMP_PORT=1936
NGINX_HTTP_PORT=8081
```

### Stream Not Playing

1. Verify the NGINX container is running
2. Check that FFmpeg restream process is active
3. Wait 10-15 seconds for HLS segments to generate
4. Try the RTMP URL instead of HLS
5. Check firewall settings for port access

## Future Enhancements

- Jellyfin integration for media library management
- Authentication and access control
- Recording management interface
- Multi-bitrate streaming controls
- Stream analytics and monitoring

## Security Notes

⚠️ **Important**: This setup is intended for local/development use. For production:

1. Enable authentication in NGINX configuration
2. Use HTTPS/SSL certificates
3. Configure firewall rules appropriately
4. Restrict publishing permissions
5. Monitor resource usage and connections