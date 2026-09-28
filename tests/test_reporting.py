import sqlite3
import unittest

from reporting.report import campaign_facts, render_detail, system_summary
from storage.db import SCHEMA


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT INTO banks(id,code,name,country,website) VALUES (1,'demo','Demo Bank','DE','https://bank.test')")
        self.conn.execute("INSERT INTO cards(id,bank_id,name,url) VALUES (1,1,'Demo Card','https://bank.test/card')")
        self.conn.executemany(
            """INSERT INTO sources(id,bank_id,source_type,url,domain,fetcher,source_role,enabled)
               VALUES (?,?,?,?,?,'requests','primary',1)""",
            [
                (1, 1, "official", "https://bank.test/offer", "bank.test"),
                (2, 1, "third_party", "https://review.test/offer", "review.test"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO raw_snapshots(id,source_id,url,fetched_at,http_status,content_hash,raw_content_path,text_content_path,extracted)
               VALUES (?,?,?,?,200,?,?,?,1)""",
            [
                (1, 1, "https://bank.test/offer", "2026-09-01T00:00:00+00:00", "h1", "raw1.html", "text1.txt"),
                (2, 2, "https://review.test/offer", "2026-09-02T00:00:00+00:00", "h2", "raw2.html", "text2.txt"),
                (3, 2, "https://review.test/offer", "2026-09-03T00:00:00+00:00", "h3", "raw3.html", "text3.txt"),
            ],
        )
        self.conn.executemany(
            """INSERT INTO campaign_extractions(
                   id,snapshot_id,bank_id,card_id,product_scope,campaign_title,campaign_type,offer_summary,
                   reward_value,conditions,start_date,end_date,language,evidence_quote,confidence,extraction_method,extracted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (1,1,1,1,"credit_card","Demo bonus","welcome_bonus","Official summary","€100","Spend €500",None,"2026-12-31","en","Official quote",0.99,"ai","2026-09-01"),
                (2,2,1,1,"credit_card","Demo bonus","welcome_bonus","Review old","€100","Spend €400",None,"2026-12-31","en","Old quote",0.95,"ai","2026-09-02"),
                (3,3,1,1,"credit_card","Demo bonus","welcome_bonus","Review latest","€100","Spend €500 and be new",None,"2026-12-31","en","Latest quote",0.97,"ai","2026-09-03"),
            ],
        )
        self.conn.execute(
            """INSERT INTO campaigns(
                   id,bank_id,card_id,product_scope,canonical_title,campaign_type,offer_summary,reward_value,
                   conditions,start_date,end_date,verification_status,lifecycle_status,primary_extraction_id,
                   verification_notes,first_seen_at,last_seen_at,last_verified_at)
               VALUES (1,1,1,'credit_card','Demo bonus','welcome_bonus','Official summary','€100','Spend €500',NULL,
                       '2026-12-31','verified','active',1,'2 independent domain(s)','2026-09-01','2026-09-03','2026-09-03')"""
        )
        self.conn.executemany(
            "INSERT INTO campaign_sources(campaign_id,extraction_id,match_method) VALUES (1,?,?)",
            [(1,"seed"),(2,"rule"),(3,"rule")],
        )

    def tearDown(self):
        self.conn.close()

    def test_fact_uses_independent_domains_not_snapshot_count(self):
        fact = campaign_facts(self.conn)[0]
        self.assertEqual(fact["verification"]["independent_source_count"], 2)
        self.assertEqual(fact["verification"]["independent_domains"], ["bank.test", "review.test"])
        self.assertEqual(len(fact["provenance"]["sources"]), 2)

    def test_provenance_keeps_latest_snapshot_and_version_count(self):
        fact = campaign_facts(self.conn)[0]
        review = next(s for s in fact["provenance"]["sources"] if s["domain"] == "review.test")
        self.assertEqual(review["version_count"], 2)
        self.assertEqual(review["latest_evidence"]["snapshot_id"], 3)
        self.assertEqual(review["latest_evidence"]["raw_snapshot_path"], "raw3.html")
        self.assertEqual(review["latest_evidence"]["evidence_quote"], "Latest quote")
        self.assertEqual([v["snapshot_id"] for v in review["evidence_history"]], [3, 2])

    def test_supporting_conditions_are_exposed_without_replacing_canonical_fact(self):
        fact = campaign_facts(self.conn)[0]
        self.assertEqual(fact["campaign"]["conditions"], "Spend €500")
        self.assertEqual(fact["campaign"]["supporting_conditions"], ["Spend €500 and be new"])

    def test_filters_and_detail_renderer(self):
        self.assertEqual(len(campaign_facts(self.conn, verified_only=True)), 1)
        self.assertEqual(len(campaign_facts(self.conn, campaign_type="cashback")), 0)
        detail = render_detail(campaign_facts(self.conn, campaign_id=1)[0])
        self.assertIn("https://bank.test/offer", detail)
        self.assertIn("raw1.html", detail)
        self.assertIn("Latest quote", detail)

    def test_system_summary(self):
        summary = system_summary(self.conn)
        self.assertEqual(summary["campaigns"]["verified"], 1)
        self.assertEqual(summary["sources"]["enabled"], 2)
        self.assertEqual(summary["pending_snapshots"], 0)


if __name__ == "__main__":
    unittest.main()

