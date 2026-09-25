"""Regression tests for discovery lead persistence/counting."""

import sqlite3
import unittest

from storage.db import SCHEMA
from storage.repo import insert_lead


class DiscoveryLeadPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO banks (id, code, name, country) VALUES (1, 'test', 'Test Bank', 'DE')"
        )

    def tearDown(self):
        self.conn.close()

    def test_insert_lead_reports_only_new_unique_row(self):
        first = insert_lead(
            self.conn, 1, "https://bank.example/promo", "https://bank.example/", "promo", 0.9
        )
        duplicate = insert_lead(
            self.conn, 1, "https://bank.example/promo", "https://bank.example/cards", "same promo", 0.95
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM discovery_leads").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()

