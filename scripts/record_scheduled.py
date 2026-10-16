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

sys.path.insert(0, str(Path(__file__).parent.parent))

from iptv_tui.domain import run_report  # noqa: E402

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


def notify_discord(message: str) -> bool:
    """Post a message to DISCORD_WEBHOOK_URL from .env; never fails the recording.

    Returns True when Discord accepted the message.
    """
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("Discord notification skipped: DISCORD_WEBHOOK_URL is not set")
        return False
    try:
        import requests

        resp = requests.post(webhook_url, json={"content": message[:1900]}, timeout=20)
        resp.raise_for_status()
        logger.info("Discord notification sent")
        return True
    except Exception as exc:
        # The exception text can include the webhook URL, which is a secret.
        logger.error(f"Discord notification failed: {type(exc).__name__}")
        return False


def generate_stream_url(server: str, username: str, password: str, stream_id: int) -> str:
    """Generate a fresh stream URL for the given stream ID."""
    return f"{server}/live/{username}/{password}/{stream_id}.ts"


RETRY_DELAY_SECONDS = 30
MIN_USEFUL_SECONDS = 60


def record_stream(stream_url: str, output_path: str, duration: int, channel_name: str = None, report=None):
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
        report: Optional RunReport; gets one entry per attempt and dropout
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

        ok, wrote_bytes, info = _record_once(stream_url, target, remaining, channel_name)
        captured_any = captured_any or wrote_bytes
        if report is not None:
            size_mb = Path(target).stat().st_size / (1024 * 1024) if Path(target).exists() else 0
            report.attempt(attempt, target, remaining, info["elapsed"], size_mb, ok, wrote_bytes, info["error"])
            # A real dropout always gets a line; repeated empty attempts (a busy
            # provider can mean hundreds) only the first few.
            if not ok and (wrote_bytes or attempt <= 3):
                reason = info["error"] or "no reason given"
                if wrote_bytes:
                    text = (f"provider dropout after {run_report.format_seconds(info['elapsed'])} "
                            f"of footage ({reason}); retrying")
                else:
                    text = f"attempt {attempt} got no data from the provider ({reason}); retrying"
                report.event("recording", run_report.WARNING, text)
        if ok:
            return True

        remaining = int(deadline - time.monotonic())
        if remaining < MIN_USEFUL_SECONDS:
            logger.error("Recording window has run out; stopping retries")
            return captured_any

        logger.warning(f"Attempt {attempt} failed; retrying in {RETRY_DELAY_SECONDS}s")
        time.sleep(min(RETRY_DELAY_SECONDS, max(0, remaining - MIN_USEFUL_SECONDS)))


PROGRESS_LOG_SECONDS = 60


def _file_seconds(path: str) -> float:
    """Footage length of a capture via ffprobe; 0 when unreadable."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return 0.0


def _record_once(stream_url: str, output_path: str, duration: int, channel_name: str = None):
    """Run one FFmpeg capture. Returns (success, wrote_any_bytes, info).

    info = {"elapsed": seconds the capture ran, "error": last error line or None}.
    """
    info = {"elapsed": 0.0, "error": None}

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

        # Log FFmpeg output. Progress lines arrive twice a second; keep one a
        # minute so the journal stays readable.
        last_progress = 0.0
        for line in process.stdout:
            line = line.strip().replace(stream_url, "<provider-url>")
            if line:
                lowered = line.lower()
                important = [
                    "error", "warning", "time=", "size=", "eof", "connection",
                    "reconnect", "invalid", "failed", "timed out", "http",
                ]
                if any(text in lowered for text in important):
                    decoder_noise = "[h264" in lowered or "[aac" in lowered or "decoding" in lowered
                    if not decoder_noise and ("error" in lowered or "failed" in lowered or "timed out" in lowered):
                        info["error"] = line
                    is_progress = "time=" in lowered and "size=" in lowered and "error" not in lowered
                    if is_progress and "lsize=" not in lowered:
                        now = time.monotonic()
                        if now - last_progress < PROGRESS_LOG_SECONDS:
                            continue
                        last_progress = now
                    logger.info(f"FFmpeg: {line}")

        process.wait()

        # A live stream sends a burst at the start, so footage can be longer
        # than the wall-clock time it took. Judge by whichever is larger.
        elapsed = time.monotonic() - started_at
        if Path(output_path).exists():
            elapsed = max(elapsed, _file_seconds(output_path))
        info["elapsed"] = elapsed
        if process.returncode == 0:
            if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
                logger.error("FFmpeg exited successfully but produced no recording")
                info["error"] = info["error"] or "ffmpeg produced no recording"
                return False, False, info

            size_mb = Path(output_path).stat().st_size / (1024 * 1024)
            logger.info(f"Output file size: {size_mb:.2f} MB")
            if elapsed < duration * 0.9:
                logger.error(
                    "Recording ended early after %.0f of %d requested seconds",
                    elapsed,
                    duration,
                )
                info["error"] = info["error"] or f"ended early after {elapsed:.0f}s"
                return False, True, info

            logger.info("Recording completed successfully!")
            return True, True, info
        else:
            logger.error(f"FFmpeg exited with code {process.returncode}")
            wrote = Path(output_path).exists() and Path(output_path).stat().st_size > 0
            info["error"] = info["error"] or f"ffmpeg exit code {process.returncode}"
            return False, wrote, info

    except FileNotFoundError:
        logger.error("FFmpeg not found. Please install FFmpeg.")
        info["error"] = "ffmpeg not installed"
        return False, False, info
    except Exception as e:
        logger.error(f"Recording failed: {e}")
        info["error"] = f"{type(e).__name__}: {e}"
        return False, False, info


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

    parser.add_argument(
        "--postprocess-library-dir",
        type=str,
        default=None,
        help=(
            "Opt in to post-recording processing: remux to MP4, move into "
            "this Jellyfin show folder with season/episode naming, and "
            "trigger a Jellyfin library scan (optional)"
        )
    )

    parser.add_argument(
        "--postprocess-show-name",
        type=str,
        default="Occupation Double",
        help="Show name used in the placed episode's filename (optional)"
    )

    parser.add_argument(
        "--notify-discord",
        action="store_true",
        help="Send a Discord message when a recording without post-processing finishes (optional)"
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

    # Start the diary for this run.
    report = run_report.RunReport(
        Path(args.output).stem,
        channel_name=args.channel_name,
        stream_id=args.stream_id,
        planned_seconds=args.duration,
        output=args.output,
        show_name=args.postprocess_show_name if args.postprocess_library_dir else None,
    )
    report.event("start", run_report.OK,
                 f"recording started: {args.channel_name or 'stream'}, "
                 f"{run_report.format_seconds(args.duration)} planned")
    _note_fallback_drive(report, args.output)

    # Update status before setup so the UI shows that the timer fired.
    if args.recording_id:
        update_recording_status(args.recording_id, "recording")

    try:
        server, username, password, _ = load_credentials()
    except RuntimeError as exc:
        logger.error(str(exc))
        if args.recording_id:
            update_recording_status(args.recording_id, "failed")
        report.event("start", run_report.FAILED, f"could not load the provider login: {exc}")
        _finish_and_notify(report, args, library=False)
        sys.exit(1)
    stream_url = generate_stream_url(server, username, password, args.stream_id)

    # Start recording
    success = record_stream(
        stream_url=stream_url,
        output_path=args.output,
        duration=args.duration,
        channel_name=args.channel_name,
        report=report,
    )

    # Update final status
    if args.recording_id:
        update_recording_status(args.recording_id, "completed" if success else "failed")

    logger.info("=" * 60)
    logger.info(f"Recording {'COMPLETED' if success else 'FAILED'}")
    logger.info("=" * 60)

    attempts = report.data["attempts"]
    captured = sum(a["captured_seconds"] for a in attempts)
    dropouts = sum(1 for a in attempts if not a["ok"] and a["wrote_data"])
    report.set_result(captured_seconds=captured, dropouts=dropouts, pieces=sum(1 for a in attempts if a["wrote_data"]))
    if success:
        report.event("recording", run_report.OK,
                     f"captured {run_report.format_seconds(captured)} in {len(attempts)} attempt(s), "
                     f"{dropouts} provider dropout(s)")
    else:
        last_error = next((a["error"] for a in reversed(attempts) if a.get("error")), None)
        report.event("recording", run_report.FAILED,
                     (f"recording failed: captured {run_report.format_seconds(captured)} of "
                      f"{run_report.format_seconds(args.duration)} planned"
                      if captured else "recording failed: nothing was captured")
                     + (f" (last error: {last_error}; {len(attempts)} attempts)" if last_error else ""))

    library = bool(args.postprocess_library_dir)
    if success and library:
        logger.info("Post-processing: starting convert + join + Jellyfin placement")
        try:
            from iptv_tui.domain import recording_pipeline

            pipeline_result = recording_pipeline.postprocess_recording(
                args.output,
                show_dir=args.postprocess_library_dir,
                show_name=args.postprocess_show_name,
                report=report,
                planned_seconds=args.duration,
            )
            logger.info(f"Post-processing result: {pipeline_result}")
            placement = pipeline_result.get("placement") or {}
            length = pipeline_result.get("length") or {}
            report.set_result(
                episode_path=placement.get("path"),
                final_seconds=length.get("actual_seconds"),
            )
        except Exception as exc:
            logger.error(f"Post-processing failed: {exc}")
            report.event("postprocess", run_report.FAILED,
                         f"post-processing crashed: {type(exc).__name__}: {exc}")
    elif success:
        # No library step: still say how much footage the pieces hold.
        try:
            from iptv_tui.domain import recording_pipeline

            actual = sum(recording_pipeline.media_seconds(seg) or 0
                         for seg in recording_pipeline.find_segments(args.output))
            ratio = actual / args.duration if args.duration else 1
            status = (run_report.OK if ratio >= recording_pipeline.LENGTH_OK
                      else run_report.WARNING if ratio >= recording_pipeline.LENGTH_FAILED
                      else run_report.FAILED)
            report.set_result(final_seconds=actual)
            report.event("length", status,
                         f"{run_report.format_seconds(actual)} recorded of "
                         f"{run_report.format_seconds(args.duration)} planned ({ratio:.0%})")
        except Exception as exc:
            logger.warning(f"Length check failed: {type(exc).__name__}: {exc}")

    _finish_and_notify(report, args, library=library)
    sys.exit(1 if report.status == run_report.FAILED else 0)


def _note_fallback_drive(report, output: str) -> None:
    """Warn in the diary when the backup drive was not usable."""
    try:
        from iptv_tui.domain import recordings

        if Path(output).resolve().parent == Path(recordings.FALLBACK_RECORDS_PATH).resolve():
            report.event("drive", run_report.WARNING,
                         "the backup drive is not writable; recorded to the local fallback folder instead")
    except Exception as exc:
        logger.warning(f"Drive check skipped: {type(exc).__name__}")


def _finish_and_notify(report, args, library: bool) -> None:
    """Close the diary and tell Discord how it went.

    Successes ping when the schedule asked for a ping (or fills a library);
    warnings and failures always ping so a problem is never silent.
    """
    status = report.status
    name = args.channel_name or "stream"
    result = report.data["result"]
    problems = report.problems()
    run_id = report.data["run_id"]
    actual = result.get("final_seconds") or result.get("captured_seconds")
    planned = run_report.format_seconds(args.duration)
    dropouts = result.get("dropouts") or 0
    dropout_note = f", {dropouts} dropout(s) repaired" if dropouts else ""

    if status == run_report.OK:
        if library and result.get("episode_path"):
            message = (f"✅ Recorded and added to Jellyfin: **{Path(result['episode_path']).stem}** "
                       f"({name}, {run_report.format_seconds(actual)} of {planned}{dropout_note})")
        else:
            message = (f"✅ Recorded: **{name}** ({run_report.format_seconds(actual)} of {planned}"
                       f"{dropout_note}) → {Path(args.output).name}")
        should_send = library or args.notify_discord
    elif status == run_report.WARNING:
        message = f"⚠️ **{name}** recorded with problems: " + "; ".join(problems[:3]) + f" (run {run_id})"
        should_send = True
    else:
        failed = [e["message"] for e in report.data["events"] if e["status"] == run_report.FAILED]
        message = f"❌ **{name}** failed: " + (failed[0] if failed else "unknown problem") + f" (run {run_id})"
        should_send = True

    if should_send:
        sent = notify_discord(message)
        report.set_result(discord_sent=sent)
        if not sent:
            report.event("discord", run_report.WARNING, "the Discord message could not be delivered")
    report.finish()


if __name__ == "__main__":
    main()
