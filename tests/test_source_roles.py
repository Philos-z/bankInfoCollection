"""Integration regression for primary/supporting discovery source semantics."""

import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from storage.db import SCHEMA
from validator.validate import validate


class SourceRoleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO banks (id, code, name, country) VALUES (1, 'bank', 'Example Bank', 'DE')"
        )
        self.conn.execute(
            """INSERT INTO sources
               (id, bank_id, source_type, url, domain, fetcher, source_role)
               VALUES (1, 1, 'official', 'https://bank.example/gold', 'bank.example', 'requests', 'primary')"""
        )
        self.conn.execute(
            """INSERT INTO sources
               (id, bank_id, source_type, url, domain, fetcher, source_role)
               VALUES (2, 1, 'official', 'https://bank.example/terms', 'bank.example', 'requests', 'supporting')"""
        )
        for snapshot_id, source_id in ((1, 1), (2, 2)):
            self.conn.execute(
                """INSERT INTO raw_snapshots
                   (id, source_id, url, fetched_at, content_hash, raw_content_path, text_content_path, extracted)
                   VALUES (?, ?, ?, '2026-09-24T00:00:00+00:00', ?, 'x', 'x', 1)""",
                (snapshot_id, source_id, f"https://bank.example/{snapshot_id}", f"hash{snapshot_id}"),
            )
        self.conn.execute("INSERT INTO cards (id, bank_id, name) VALUES (1, 1, 'Gold Card')")
        self.conn.execute("INSERT INTO cards (id, bank_id, name) VALUES (2, 1, 'Platinum Card')")
        self._insert_extraction(1, 1, 1, "Gold welcome bonus", "50,000 points")
        self._insert_extraction(2, 2, 1, "Gold welcome bonus terms", "50,000 points")
        self._insert_extraction(3, 2, 2, "Platinum welcome bonus terms", "85,000 points")

    def _insert_extraction(self, extraction_id, snapshot_id, card_id, title, reward):
        self.conn.execute(
            """INSERT INTO campaign_extractions
               (id, snapshot_id, bank_id, card_id, product_scope, campaign_title, campaign_type,
                offer_summary, reward_value, confidence, extraction_method, extracted_at)
               VALUES (?, ?, 1, ?, 'credit_card', ?, 'welcome_bonus', ?, ?, 0.99, 'ai', ? )""",
            (
                extraction_id, snapshot_id, card_id, title, title, reward,
                f"2026-09-24T00:0{extraction_id}:00+00:00",
            ),
        )

    @contextmanager
    def _session(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def tearDown(self):
        self.conn.close()

    def test_supporting_source_attaches_but_cannot_seed_new_campaign(self):
        with patch("validator.validate.session", self._session), patch(
            "validator.validate.ai_arbiter.same_campaign", return_value=False
        ):
            stats = validate("bank", rebuild=True)

        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["linked"], 1)
        self.assertEqual(stats["supporting_unmatched"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM campaign_sources").fetchone()[0], 2)
        card_name = self.conn.execute(
            """SELECT cd.name FROM campaigns c LEFT JOIN cards cd ON cd.id=c.card_id"""
        ).fetchone()[0]
        self.assertEqual(card_name, "Gold Card")


if __name__ == "__main__":
    unittest.main()
