"""Smoke tests for domain modules."""

import os
import tempfile
import unittest
from pathlib import Path

from new_iptv.domain import config, db


class ConfigSmokeTest(unittest.TestCase):
    def test_config_has_required_fields(self):
        # We cannot assert values without controlling .env, but we can assert types.
        self.assertIsInstance(config.Config.IPTV_SERVER_URL, str)
        self.assertIsInstance(config.Config.IPTV_USERNAME, str)
        self.assertIsInstance(config.Config.IPTV_PASSWORD, str)


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
