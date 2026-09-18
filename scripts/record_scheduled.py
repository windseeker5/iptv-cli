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
import threading
import time
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
        raise RuntimeError(f".env file not found at {env_path}")

    load_dotenv(env_path)

    server = os.getenv("IPTV_SERVER_URL")
    username = os.getenv("IPTV_USERNAME")
    password = os.getenv("IPTV_PASSWORD")
    records_path = os.getenv("USB_RECORDS_PATH", "/mnt/media/RECORDS")

    if not all([server, username, password]):
        raise RuntimeError("Missing IPTV credentials in .env file")

    return server, username, password, records_path


def generate_stream_url(server: str, username: str, password: str, stream_id: int) -> str:
    """Generate a fresh stream URL for the given stream ID."""
    return f"{server}/live/{username}/{password}/{stream_id}.ts"


RETRY_DELAY_SECONDS = 30
MIN_USEFUL_SECONDS = 60


def record_stream(stream_url: str, output_path: str, duration: int, channel_name: str = None):
    """Record a stream, retrying until the intended end time.

    The provider refuses new connections with HTTP 458 when the account's
    connection limit is already in use, and FFmpeg gives up on that in under a
    second. Retrying until the show would have ended means a recording survives
    a slot that is busy at start time or drops out midway.

    Args:
        stream_url: The IPTV stream URL
        output_path: Path to save the recording
        duration: Duration in seconds
        channel_name: Optional channel name for metadata
    """
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + duration
    attempt = 0
    captured_any = False

    while True:
        attempt += 1
        remaining = int(deadline - time.monotonic())
        if remaining < MIN_USEFUL_SECONDS:
            logger.error("Giving up: less than a minute of the requested window remains")
            return captured_any

        # Keep earlier footage by writing each retry that follows real capture
        # to its own part file.
        target = output_path
        if captured_any:
            target = str(Path(output_path).with_suffix(f".part{attempt}.ts"))

        if attempt > 1:
            logger.info(
                f"Retry {attempt - 1}: {remaining}s of the window left, recording to {target}"
            )

        ok, wrote_bytes = _record_once(stream_url, target, remaining, channel_name)
        captured_any = captured_any or wrote_bytes
        if ok:
            return True

        remaining = int(deadline - time.monotonic())
        if remaining < MIN_USEFUL_SECONDS:
            logger.error("Recording window has run out; stopping retries")
            return captured_any

        logger.warning(f"Attempt {attempt} failed; retrying in {RETRY_DELAY_SECONDS}s")
        time.sleep(min(RETRY_DELAY_SECONDS, max(0, remaining - MIN_USEFUL_SECONDS)))


def _record_once(stream_url: str, output_path: str, duration: int, channel_name: str = None):
    """Run one FFmpeg capture. Returns (success, wrote_any_bytes)."""

    # Build FFmpeg command with reconnect options for reliability
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output file if exists
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_delay_max", "30",
        "-rw_timeout", "30000000",
        "-i", stream_url,
        "-c", "copy",  # Copy codec (no transcoding)
        "-t", str(duration),
    ]

    # Add metadata if channel name provided
    if channel_name:
        cmd.extend(["-metadata", f"title={channel_name}"])
        cmd.extend(["-metadata", f"date={datetime.now().strftime('%Y-%m-%d')}"])

    cmd.append(output_path)

    logger.info("Starting recording...")
    logger.info("  Stream: provider URL hidden because it contains credentials")
    logger.info(f"  Output: {output_path}")
    logger.info(f"  Duration: {duration} seconds ({duration // 3600}h {(duration % 3600) // 60}m)")

    try:
        started_at = time.monotonic()
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True
        )

        def stop_if_stalled() -> None:
            try:
                process.wait(timeout=duration + 300)
            except subprocess.TimeoutExpired:
                logger.error("Recording exceeded its deadline, probably due to a stalled stream")
                process.terminate()

        threading.Thread(target=stop_if_stalled, daemon=True).start()

        # Log FFmpeg output
        for line in process.stdout:
            line = line.strip().replace(stream_url, "<provider-url>")
            if line:
                important = [
                    "error", "warning", "time=", "size=", "eof", "connection",
                    "reconnect", "invalid", "failed", "timed out", "http",
                ]
                if any(text in line.lower() for text in important):
                    logger.info(f"FFmpeg: {line}")

        process.wait()

        elapsed = time.monotonic() - started_at
        if process.returncode == 0:
            if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
                logger.error("FFmpeg exited successfully but produced no recording")
                return False, False

            size_mb = Path(output_path).stat().st_size / (1024 * 1024)
            logger.info(f"Output file size: {size_mb:.2f} MB")
            if elapsed < duration * 0.9:
                logger.error(
                    "Recording ended early after %.0f of %d requested seconds",
                    elapsed,
                    duration,
                )
                return False, True

            logger.info("Recording completed successfully!")
            return True, True
        else:
            logger.error(f"FFmpeg exited with code {process.returncode}")
            wrote = Path(output_path).exists() and Path(output_path).stat().st_size > 0
            return False, wrote

    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install FFmpeg.")
        return False, False
    except Exception as e:
        logger.error(f"Recording failed: {e}")
        return False, False


def update_recording_status(recording_id: int, status: str):
    """Update the recording status in the database."""
    try:
        import sqlite3
        db_path = Path(__file__).parent.parent / "data" / "iptv.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE scheduled_recordings SET status = ?
                WHERE id = ? AND status != 'cancelled'
                """,
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

    # Update status before setup so the UI shows that the timer fired.
    if args.recording_id:
        update_recording_status(args.recording_id, "recording")

    try:
        server, username, password, _ = load_credentials()
    except RuntimeError as exc:
        logger.error(str(exc))
        if args.recording_id:
            update_recording_status(args.recording_id, "failed")
        sys.exit(1)
    stream_url = generate_stream_url(server, username, password, args.stream_id)

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
