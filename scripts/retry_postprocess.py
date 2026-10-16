#!/usr/bin/env python3
"""Rebuild a library episode from the recording pieces still on disk.

Started by JP (agent_actions.execute) after the owner taps Confirm:
    retry_postprocess.py <run id>
Writes its own run report (<run id>-retry-HHMM) and pings Discord when done.
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from iptv_tui.domain import notify, recording_pipeline, run_report, schedules  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("retry")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: retry_postprocess.py <run id>", file=sys.stderr)
        return 2
    run_id = sys.argv[1]
    original = run_report.load(run_id)
    if not original or not original.get("show_name") or not original.get("output"):
        logger.error("no usable report for %s", run_id)
        return 1
    show = original["show_name"]
    planned = original.get("planned_seconds")
    air = date.fromisoformat(original["started_at"][:10])
    report = run_report.RunReport(
        f"{run_id}-retry-{datetime.now():%H%M}",
        channel_name=original.get("channel_name"), planned_seconds=planned,
        output=original["output"], show_name=show, retry_of=run_id,
    )
    report.event("start", run_report.OK, f"rebuilding “{show}” of {air} from the pieces saved by {run_id}")
    try:
        result = recording_pipeline.postprocess_recording(
            original["output"], show_dir=str(Path(schedules.tv_library_root()) / show), show_name=show,
            report=report, planned_seconds=planned, air_date=air)
    except Exception as exc:
        report.event("postprocess", run_report.FAILED, f"rebuild crashed: {type(exc).__name__}: {exc}")
        result = {}
    placed = (result.get("placement") or {}).get("path")
    if placed:
        report.set_result(episode_path=placed, final_seconds=(result.get("length") or {}).get("actual_seconds"))
    report.finish()

    name = original.get("channel_name") or show
    if report.status == run_report.OK and placed:
        msg = f"✅ Rebuilt and added to Jellyfin: **{Path(placed).stem}**"
    elif placed:
        msg = f"⚠️ Rebuilt **{Path(placed).stem}** with problems: " + "; ".join(report.problems()[:3])
    else:
        failed = [e["message"] for e in report.data["events"] if e["status"] == run_report.FAILED]
        msg = f"❌ Could not rebuild **{name}**: " + (failed[0] if failed else "unknown problem")
    notify.discord(msg)
    return 0 if placed else 1


if __name__ == "__main__":
    sys.exit(main())
