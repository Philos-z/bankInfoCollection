"""Offline end-to-end regression for the Amex duplicate-resolution path.

This test uses an in-memory SQLite database with production schema and runs
validator.validate.validate() over representative Amex extraction records.
No network or live AI is used; the ambiguous BMW alias decision is mocked.
"""

import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from storage.db import SCHEMA
from validator.validate import validate


class AmexEntityResolutionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT INTO banks (id, code, name, country, website) VALUES (1, 'amex_de', 'American Express Deutschland', 'DE', 'https://americanexpress.com')"
        )
        self.conn.execute(
            "INSERT INTO sources (id, bank_id, source_type, url, domain, fetcher) VALUES (1, 1, 'official', 'https://americanexpress.com/bonus', 'americanexpress.com', 'requests')"
        )
        self.conn.execute(
            "INSERT INTO sources (id, bank_id, source_type, url, domain, fetcher) VALUES (2, 1, 'third_party', 'https://review.example/amex', 'review.example', 'requests')"
        )
        self.conn.execute(
            """INSERT INTO raw_snapshots
               (id, source_id, url, fetched_at, content_hash, raw_content_path, text_content_path, extracted)
               VALUES (1, 1, 'https://americanexpress.com/bonus', '2026-09-23T20:00:00+00:00', 'official', 'x', 'x', 1)"""
        )
        self.conn.execute(
            """INSERT INTO raw_snapshots
               (id, source_id, url, fetched_at, content_hash, raw_content_path, text_content_path, extracted)
               VALUES (2, 2, 'https://review.example/amex', '2026-09-23T20:01:00+00:00', 'thirdparty', 'x', 'x', 1)"""
        )
        self._next_card_id = 1
        self._cards = {}
        self._next_extraction_id = 1

    def tearDown(self):
        self.conn.close()

    def _card_id(self, name):
        if name is None:
            return None
        if name not in self._cards:
            card_id = self._next_card_id
            self._next_card_id += 1
            self.conn.execute("INSERT INTO cards (id, bank_id, name) VALUES (?, 1, ?)", (card_id, name))
            self._cards[name] = card_id
        return self._cards[name]

    def _extraction(
        self,
        *,
        snapshot_id=1,
        card_name,
        title,
        reward,
        campaign_type="welcome_bonus",
        confidence=0.99,
    ):
        extraction_id = self._next_extraction_id
        self._next_extraction_id += 1
        self.conn.execute(
            """INSERT INTO campaign_extractions
               (id, snapshot_id, bank_id, card_id, product_scope, campaign_title, campaign_type,
                offer_summary, reward_value, conditions, start_date, end_date, language,
                evidence_quote, confidence, extraction_method, extracted_at)
               VALUES (?, ?, 1, ?, 'credit_card', ?, ?, ?, ?, NULL, NULL, NULL, 'de', ?, ?, 'ai', ?)""",
            (
                extraction_id,
                snapshot_id,
                self._card_id(card_name),
                title,
                campaign_type,
                title,
                reward,
                title,
                confidence,
                f"2026-09-23T20:{extraction_id:02d}:00+00:00",
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

    def _seed_fixture(self):
        # Five same-page duplicate pairs reproduced from the real Amex official snapshot.
        self._extraction(card_name="American Express Blue Card", title="25 Euro Startguthaben", reward="25 EUR")
        self._extraction(card_name="American Express Blue Card", title="25 Euro Startguthaben", reward="25 Euro")
        self._extraction(card_name="PAYBACK American Express Karte", title="1.000 PAYBACK Punkte", reward="1,000 PAYBACK points")
        self._extraction(card_name="PAYBACK American Express Karte", title="1.000 PAYBACK Extra-Punkte", reward="1.000 PAYBACK points")
        self._extraction(card_name="BMW Premium Card Carbon", title="37.500 Membership Rewards Punkte", reward="37,500 points", confidence=0.97)
        self._extraction(card_name="BMW Card Carbon", title="37.500 Membership Rewards Punkte Willkommensbonus", reward="37.500 points")
        self._extraction(card_name="BMW Card", title="11.000 Membership Rewards Punkte", reward="11,000 points")
        self._extraction(card_name="BMW Card", title="11.000 Membership Rewards Punkte Willkommensbonus", reward="11.000 points")
        self._extraction(card_name="American Express Blue Card", title="5.000 ExtraPunkte", reward="5,000 points")
        self._extraction(card_name="American Express Blue Card", title="5.000 Membership Rewards Punkte", reward="5.000 points")

        # Gold and Gold Rosé share identical mechanics but must stay separate entities.
        self._extraction(card_name="American Express Gold Card", title="Bis zu 50.000 Membership Rewards Punkte", reward="50,000 points")
        self._extraction(card_name="American Express Gold Card Rosé", title="Bis zu 50.000 Membership Rewards Punkte", reward="50,000 points", confidence=0.98)

        # Independent third-party evidence should attach to each distinct Gold entity.
        self._extraction(snapshot_id=2, card_name="American Express Gold Card", title="Bis zu 50.000 Membership Rewards Punkte", reward="50.000 points")
        self._extraction(snapshot_id=2, card_name="American Express Rosé Gold Card", title="Bis zu 50.000 Membership Rewards Punkte", reward="50.000 points")

    def test_rebuild_deduplicates_known_pairs_without_merging_gold_and_rose(self):
        self._seed_fixture()

        def arbiter(a, b):
            names = {a.get("card_name"), b.get("card_name")}
            return names == {"BMW Premium Card Carbon", "BMW Card Carbon"}

        with patch("validator.validate.session", self._session), patch(
            "validator.validate.ai_arbiter.same_campaign", side_effect=arbiter
        ):
            stats = validate("amex_de", rebuild=True)

        campaigns = [dict(r) for r in self.conn.execute(
            """SELECT c.id, cd.name AS card_name, c.reward_value, c.verification_status
                 FROM campaigns c LEFT JOIN cards cd ON cd.id = c.card_id
                WHERE c.bank_id = 1 ORDER BY c.id"""
        )]
        self.assertEqual(len(campaigns), 7)
        self.assertEqual(stats["created"], 7)
        self.assertEqual(stats["linked"], 7)
        self.assertEqual(stats["ai_matches"], 1)

        gold = [c for c in campaigns if c["card_name"] == "American Express Gold Card"]
        rose = [c for c in campaigns if "Rosé" in (c["card_name"] or "")]
        self.assertEqual(len(gold), 1)
        self.assertEqual(len(rose), 1)
        self.assertEqual(gold[0]["verification_status"], "verified")
        self.assertEqual(rose[0]["verification_status"], "verified")

        supporter_counts = {
            r["card_name"]: r["n"]
            for r in self.conn.execute(
                """SELECT cd.name AS card_name, COUNT(cs.extraction_id) AS n
                     FROM campaigns c
                     LEFT JOIN cards cd ON cd.id = c.card_id
                     JOIN campaign_sources cs ON cs.campaign_id = c.id
                    WHERE c.bank_id = 1
                    GROUP BY c.id, cd.name"""
            )
        }
        self.assertEqual(supporter_counts["American Express Blue Card"], 2)
        self.assertEqual(supporter_counts["PAYBACK American Express Karte"], 2)
        self.assertEqual(supporter_counts["BMW Card Carbon"], 2)


if __name__ == "__main__":
    unittest.main()

