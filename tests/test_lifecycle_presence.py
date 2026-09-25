"""Regression tests for lifecycle protection against transient extraction misses."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from storage.db import SCHEMA
from validator.validate import _refresh_campaign


class LifecyclePresenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.conn.execute("INSERT INTO banks (id, code, name, country) VALUES (1, 'x', 'Example', 'DE')")
        self.conn.execute(
            """INSERT INTO sources (id, bank_id, source_type, url, domain, fetcher, enabled)
               VALUES (1, 1, 'official', 'https://example.test', 'example.test', 'requests', 1)"""
        )
        self.conn.execute(
            """INSERT INTO raw_snapshots
               (id, source_id, url, fetched_at, content_hash, raw_content_path, text_content_path, extracted)
               VALUES (1, 1, 'https://example.test', '2026-09-24T00:00:00+00:00', 'old', 'old.html', 'old.txt', 1)"""
        )
        self.conn.execute(
            """INSERT INTO raw_snapshots
               (id, source_id, url, fetched_at, content_hash, raw_content_path, text_content_path, extracted)
               VALUES (2, 1, 'https://example.test', '2026-09-25T00:00:00+00:00', 'new', 'new.html', 'new.txt', 1)"""
        )
        self.conn.execute(
            """INSERT INTO campaign_extractions
               (id, snapshot_id, bank_id, product_scope, campaign_title, campaign_type, reward_value,
                evidence_quote, confidence, extraction_method, extracted_at)
               VALUES (1, 1, 1, 'credit_card', 'Welcome offer', 'welcome_bonus', '100 EUR',
                       'Get 100 EUR after qualifying spend.', 0.99, 'ai', '2026-09-24T00:00:00+00:00')"""
        )
        self.conn.execute(
            """INSERT INTO campaigns
               (id, bank_id, product_scope, canonical_title, campaign_type, reward_value,
                verification_status, lifecycle_status, primary_extraction_id, first_seen_at, last_seen_at)
               VALUES (1, 1, 'credit_card', 'Welcome offer', 'welcome_bonus', '100 EUR',
                       'unverified', 'active', 1, '2026-09-24T00:00:00+00:00', '2026-09-24T00:00:00+00:00')"""
        )
        self.conn.execute(
            "INSERT INTO campaign_sources (campaign_id, extraction_id, match_method) VALUES (1, 1, 'seed')"
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_missing_current_extraction_stays_active_when_evidence_is_still_present(self):
        (self.root / "new.txt").write_text(
            "Current page content. Get 100 EUR after qualifying spend. More terms.", encoding="utf-8"
        )
        with patch("validator.validate.settings.ROOT", self.root):
            _refresh_campaign(self.conn, 1, [])
        row = self.conn.execute("SELECT lifecycle_status FROM campaigns WHERE id=1").fetchone()
        self.assertEqual(row["lifecycle_status"], "active")

    def test_campaign_is_removed_when_evidence_disappears_from_latest_page(self):
        (self.root / "new.txt").write_text("Current page no longer contains the promotion.", encoding="utf-8")
        with patch("validator.validate.settings.ROOT", self.root):
            _refresh_campaign(self.conn, 1, [])
        row = self.conn.execute("SELECT lifecycle_status FROM campaigns WHERE id=1").fetchone()
        self.assertEqual(row["lifecycle_status"], "removed")


if __name__ == "__main__":
    unittest.main()
