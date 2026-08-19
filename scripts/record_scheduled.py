#!/usr/bin/env python3
"""
Standalone Scheduled Recording Script for IPTV

This script is executed by systemd at scheduled times to record IPTV streams.
It runs independently of the main IPTV application.

Usage:
    python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /path/to/output.ts
    python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /path/to/output.ts --channel-name "RDS Sports"
"""

import argparse
import os
import sys
import subprocess
import logging
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def load_credentials():
    """Load IPTV credentials from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        logger.error(f".env file not found at {env_path}")
        sys.exit(1)

    load_dotenv(env_path)

    server = os.getenv("IPTV_SERVER_URL")
    username = os.getenv("IPTV_USERNAME")
    password = os.getenv("IPTV_PASSWORD")
    records_path = os.getenv("USB_RECORDS_PATH", "/mnt/media/RECORDS")

    if not all([server, username, password]):
        logger.error("Missing IPTV credentials in .env file")
        sys.exit(1)

    return server, username, password, records_path


def generate_stream_url(server: str, username: str, password: str, stream_id: int) -> str:
    """Generate a fresh stream URL for the given stream ID."""
    return f"{server}/live/{username}/{password}/{stream_id}.ts"


def record_stream(stream_url: str, output_path: str, duration: int, channel_name: str = None):
    """
    Record a stream using FFmpeg.

    Args:
        stream_url: The IPTV stream URL
        output_path: Path to save the recording
        duration: Duration in seconds
        channel_name: Optional channel name for metadata
    """
    # Ensure output directory exists
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build FFmpeg command with reconnect options for reliability
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output file if exists
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "30",
        "-i", stream_url,
        "-c", "copy",  # Copy codec (no transcoding)
        "-t", str(duration),
    ]

    # Add metadata if channel name provided
    if channel_name:
        cmd.extend(["-metadata", f"title={channel_name}"])
        cmd.extend(["-metadata", f"date={datetime.now().strftime('%Y-%m-%d')}"])

    cmd.append(output_path)

    logger.info(f"Starting recording...")
    logger.info(f"  Stream: {stream_url[:50]}...")
    logger.info(f"  Output: {output_path}")
    logger.info(f"  Duration: {duration} seconds ({duration // 3600}h {(duration % 3600) // 60}m)")

    try:
        # Run FFmpeg and capture output
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )

        # Log FFmpeg output
        for line in process.stdout:
            line = line.strip()
            if line:
                # Only log important lines (progress, errors)
                if any(x in line.lower() for x in ['error', 'warning', 'time=', 'size=']):
                    logger.info(f"FFmpeg: {line}")

        process.wait()

        if process.returncode == 0:
            logger.info(f"Recording completed successfully!")
            # Check file size
            if Path(output_path).exists():
                size_mb = Path(output_path).stat().st_size / (1024 * 1024)
                logger.info(f"Output file size: {size_mb:.2f} MB")
            return True
        else:
            logger.error(f"FFmpeg exited with code {process.returncode}")
            return False

    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install FFmpeg.")
        return False
    except Exception as e:
        logger.error(f"Recording failed: {e}")
        return False


def update_recording_status(recording_id: int, status: str):
    """Update the recording status in the database."""
    try:
        import sqlite3
        db_path = Path(__file__).parent.parent / "data" / "iptv.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE scheduled_recordings SET status = ? WHERE id = ?",
                (status, recording_id)
            )
            conn.commit()
            conn.close()
            logger.info(f"Updated recording {recording_id} status to '{status}'")
    except Exception as e:
        logger.warning(f"Could not update database status: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Record an IPTV stream",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Record stream ID 12345 for 1 hour
    python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /mnt/media/RECORDS/hockey.ts

    # Record with channel name metadata
    python3 record_scheduled.py --stream-id 12345 --duration 10800 --output /mnt/media/RECORDS/rds.ts --channel-name "RDS Sports"

    # Record with database tracking
    python3 record_scheduled.py --stream-id 12345 --duration 3600 --output /mnt/media/RECORDS/test.ts --recording-id 42
        """
    )

    parser.add_argument(
        "--stream-id", "-s",
        type=int,
        required=True,
        help="IPTV stream ID to record"
    )

    parser.add_argument(
        "--duration", "-d",
        type=int,
        required=True,
        help="Recording duration in seconds"
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        required=True,
        help="Output file path (e.g., /mnt/media/RECORDS/hockey.ts)"
    )

    parser.add_argument(
        "--channel-name", "-n",
        type=str,
        default=None,
        help="Channel name for metadata (optional)"
    )

    parser.add_argument(
        "--recording-id", "-r",
        type=int,
        default=None,
        help="Database recording ID for status updates (optional)"
    )

    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("IPTV Scheduled Recording Started")
    logger.info("=" * 60)
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Stream ID: {args.stream_id}")
    logger.info(f"Duration: {args.duration}s")
    logger.info(f"Output: {args.output}")
    if args.channel_name:
        logger.info(f"Channel: {args.channel_name}")

    # Load credentials and generate stream URL
    server, username, password, _ = load_credentials()
    stream_url = generate_stream_url(server, username, password, args.stream_id)

    # Update status to recording if tracking enabled
    if args.recording_id:
        update_recording_status(args.recording_id, "recording")

    # Start recording
    success = record_stream(
        stream_url=stream_url,
        output_path=args.output,
        duration=args.duration,
        channel_name=args.channel_name
    )

    # Update final status
    if args.recording_id:
        update_recording_status(args.recording_id, "completed" if success else "failed")

    logger.info("=" * 60)
    logger.info(f"Recording {'COMPLETED' if success else 'FAILED'}")
    logger.info("=" * 60)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
