"""Offline regression tests for source-health state and thresholds."""

import sqlite3
import unittest

from crawler import source_health
from storage.db import SCHEMA
from storage.repo import record_source_failure, record_source_success


class SourceHealthLevelTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(source_health.health_level(0, enabled=False), "DISABLED")
        self.assertEqual(source_health.health_level(0, last_attempt_at=None), "UNKNOWN")
        self.assertEqual(source_health.health_level(0, last_attempt_at="x"), "OK")
        self.assertEqual(source_health.health_level(1, last_attempt_at="x"), "DEGRADED")
        self.assertEqual(source_health.health_level(2, last_attempt_at="x"), "DEGRADED")
        self.assertEqual(source_health.health_level(3, last_attempt_at="x"), "WARNING")
        self.assertEqual(source_health.health_level(4, last_attempt_at="x"), "WARNING")
        self.assertEqual(source_health.health_level(5, last_attempt_at="x"), "CRITICAL")


class SourceHealthPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO banks (id, code, name, country) VALUES (1, 'x', 'X', 'DE')")
        self.conn.execute(
            """INSERT INTO sources (id, bank_id, source_type, url, domain, fetcher)
               VALUES (1, 1, 'official', 'https://example.test', 'example.test', 'requests')"""
        )

    def tearDown(self):
        self.conn.close()

    def test_failures_accumulate_and_success_resets(self):
        self.assertEqual(record_source_failure(self.conn, 1, "timeout", None, 1200), 1)
        self.assertEqual(record_source_failure(self.conn, 1, "HTTP 500", 500, 900), 2)
        row = self.conn.execute("SELECT * FROM sources WHERE id=1").fetchone()
        self.assertEqual(row["consecutive_failures"], 2)
        self.assertEqual(row["last_http_status"], 500)
        self.assertEqual(row["last_latency_ms"], 900)
        self.assertEqual(row["last_error"], "HTTP 500")
        self.assertIsNotNone(row["last_attempt_at"])
        self.assertIsNone(row["last_success_at"])

        record_source_success(self.conn, 1, 200, 350)
        row = self.conn.execute("SELECT * FROM sources WHERE id=1").fetchone()
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_http_status"], 200)
        self.assertEqual(row["last_latency_ms"], 350)
        self.assertIsNone(row["last_error"])
        self.assertIsNotNone(row["last_success_at"])


if __name__ == "__main__":
    unittest.main()
