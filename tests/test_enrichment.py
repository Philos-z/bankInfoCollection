import json
import sqlite3
import unittest
from unittest.mock import patch

from reporting import enrich
from storage.db import SCHEMA


class EnrichmentValidationTests(unittest.TestCase):
    def _evidence(self):
        return [{
            "extraction_id": 10,
            "snapshot_id": 20,
            "campaign_title": "Plan 1060",
            "offer_summary": "Earn up to €1,060 in one year",
            "reward_value": "Hasta 1.060 euros",
            "conditions": "Salary at least €800 for €400; card use gives €120",
            "start_date": "2026-06-09",
            "end_date": "2026-10-20",
            "evidence_quote": "1.060 euros ... salario mínimo 800 ... 400 euros ... 120 euros",
            "confidence": 0.98,
        }]

    def test_accepts_equivalent_number_formatting(self):
        result = {
            "headline": {"text": "Get up to €1,060", "evidence_refs": [10]},
            "maximum_reward": {"text": "€1,060", "evidence_refs": [10]},
            "eligibility": [], "required_steps": [], "optional_steps": [],
            "action_rewards": [{
                "key": "salary", "action": "Deposit salary", "requirement": "At least €800",
                "reward": "€400", "optional": True, "evidence_refs": [10],
            }],
            "deadline": {"text": "2026-10-20", "evidence_refs": [10]},
            "reward_period": None, "exclusions": [], "warnings": [],
        }
        facts = enrich._validate_result(result, self._evidence())
        self.assertEqual(len(facts), 4)

    def test_accepts_equivalent_decimal_trailing_zero(self):
        evidence = self._evidence()
        evidence[0]["evidence_quote"] += " net 858,6 euros"
        result = {
            "headline": {"text": "Net value €858.60", "evidence_refs": [10]},
            "maximum_reward": None, "eligibility": [], "required_steps": [], "action_rewards": [],
            "optional_steps": [], "deadline": None, "reward_period": None, "exclusions": [], "warnings": [],
        }
        facts = enrich._validate_result(result, evidence)
        self.assertEqual(len(facts), 1)

    def test_rejects_unknown_evidence_reference(self):
        result = {
            "headline": {"text": "Get €1,060", "evidence_refs": [999]},
            "maximum_reward": None, "eligibility": [], "required_steps": [], "action_rewards": [],
            "optional_steps": [], "deadline": None, "reward_period": None, "exclusions": [], "warnings": [],
        }
        with self.assertRaisesRegex(ValueError, "outside campaign"):
            enrich._validate_result(result, self._evidence())

    def test_rejects_unsupported_numeric_claim(self):
        result = {
            "headline": {"text": "Get €9,999", "evidence_refs": [10]},
            "maximum_reward": None, "eligibility": [], "required_steps": [], "action_rewards": [],
            "optional_steps": [], "deadline": None, "reward_period": None, "exclusions": [], "warnings": [],
        }
        with self.assertRaisesRegex(ValueError, "unsupported numeric"):
            enrich._validate_result(result, self._evidence())


class EnrichmentPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO banks(id,code,name,country) VALUES(1,'demo','Demo Bank','DE')")
        self.conn.execute("INSERT INTO sources(id,bank_id,source_type,url,domain,fetcher,source_role,enabled) VALUES(1,1,'official','https://x.test','x.test','requests','primary',1)")
        self.conn.execute("INSERT INTO raw_snapshots(id,source_id,url,fetched_at,http_status,content_hash,raw_content_path,text_content_path,extracted) VALUES(1,1,'https://x.test','2026-09-01',200,'h','r.html','t.txt',1)")
        self.conn.execute("INSERT INTO campaign_extractions(id,snapshot_id,bank_id,product_scope,campaign_title,campaign_type,offer_summary,reward_value,conditions,evidence_quote,confidence,extraction_method,extracted_at) VALUES(1,1,1,'card_bundle','Offer','welcome_bonus','Get €100','€100','New customer','Get 100 euros',0.99,'ai','2026-09-01')")
        self.conn.execute("INSERT INTO campaigns(id,bank_id,product_scope,canonical_title,campaign_type,offer_summary,reward_value,conditions,verification_status,lifecycle_status,primary_extraction_id,first_seen_at,last_seen_at) VALUES(1,1,'card_bundle','Offer','welcome_bonus','Get €100','€100','New customer','verified','active',1,'2026-09-01','2026-09-01')")
        self.conn.execute("INSERT INTO campaign_sources(campaign_id,extraction_id,match_method) VALUES(1,1,'seed')")

    def tearDown(self):
        self.conn.close()

    def test_persist_replaces_facts_and_binds_snapshot(self):
        extractions = [{"extraction_id": 1, "snapshot_id": 1}]
        facts = [("headline", {"text": "Get €100"}, [1], 0.99)]
        enrich._persist(self.conn, 1, "hash1", {"headline": {}}, facts, extractions)
        fact = self.conn.execute("SELECT * FROM campaign_facts WHERE campaign_id=1").fetchone()
        evidence = self.conn.execute("SELECT * FROM fact_evidence WHERE fact_id=?", (fact["id"],)).fetchone()
        self.assertEqual(json.loads(fact["value_json"])["text"], "Get €100")
        self.assertEqual(evidence["extraction_id"], 1)
        self.assertEqual(evidence["snapshot_id"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM campaign_enrichments WHERE campaign_id=1").fetchone()[0], "complete")

    def test_load_marks_facts_stale_after_campaign_changes(self):
        campaign, extractions = enrich._campaign_input(self.conn, 1)
        fingerprint = enrich._input_hash(campaign, extractions)
        enrich._persist(
            self.conn, 1, fingerprint, {"headline": {}},
            [("headline", {"text": "Get €100"}, [1], 0.99)], extractions,
        )
        self.assertEqual(enrich.load_enrichment(self.conn, 1)["status"], "complete")
        self.conn.execute("UPDATE campaigns SET conditions='Changed requirement' WHERE id=1")
        self.assertEqual(enrich.load_enrichment(self.conn, 1)["status"], "stale")


if __name__ == "__main__":
    unittest.main()
