"""Smoke tests for domain modules."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from iptv_tui.domain import config, db, recordings


class ConfigSmokeTest(unittest.TestCase):
    def test_config_has_required_fields(self):
        # We cannot assert values without controlling .env, but we can assert types.
        self.assertIsInstance(config.Config.IPTV_SERVER_URL, str)
        self.assertIsInstance(config.Config.IPTV_USERNAME, str)
        self.assertIsInstance(config.Config.IPTV_PASSWORD, str)


class RecordingSmokeTest(unittest.TestCase):
    def test_systemd_command_tracks_database_recording(self):
        cmd, unit = recordings.build_systemd_command(
            stream_id=123,
            channel_name="Test Channel",
            duration_seconds=1800,
            output_path="/tmp/test.ts",
            start_time=datetime.now(),
            start_now=True,
            recording_id=42,
            timer_unit="iptv-test",
        )
        self.assertEqual(unit, "iptv-test")
        self.assertIn("--recording-id", cmd)
        self.assertIn("42", cmd)
        self.assertIn("--property=RuntimeMaxSec=2100", cmd)


class DbSmokeTest(unittest.TestCase):
    def test_init_db_creates_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = db.DEFAULT_DATA_DIR
            try:
                db.DEFAULT_DATA_DIR = Path(tmp)
                db.init_db()
                tables = db.list_tables()
                self.assertIn("live_streams", tables)
                self.assertIn("vod_streams", tables)
                self.assertIn("scheduled_recordings", tables)
            finally:
                db.DEFAULT_DATA_DIR = original


if __name__ == "__main__":
    unittest.main()
