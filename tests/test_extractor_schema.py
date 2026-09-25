"""Regression tests for extraction cleanup and hallucination guards.

See tests/README.md for the business rationale behind these assertions.
"""

import unittest

from extractor.schema import clean_campaign, normalize_date, evidence_quote_valid
from extractor.extractor import _dedupe_bundle_variants


class NormalizeDateTests(unittest.TestCase):
    def test_accepts_iso_date(self):
        self.assertEqual(normalize_date("2026-12-31"), "2026-12-31")

    def test_rejects_non_iso_or_impossible_dates(self):
        for value in ("31.12.2026", "2026/12/31", "2026-02-30", "", None, 20261231):
            with self.subTest(value=value):
                self.assertIsNone(normalize_date(value))


class CleanCampaignTests(unittest.TestCase):
    SOURCE = "Get 50,000 points now. Offer valid until 31 December 2026."

    def _raw(self, **overrides):
        raw = {
            "product_scope": "credit_card",
            "card_name": "  Gold Card  ",
            "campaign_title": "  Gold welcome bonus  ",
            "campaign_type": "welcome_bonus",
            "offer_summary": "Earn 50,000 points.",
            "reward_value": "50,000 points",
            "conditions": "New cardholders only.",
            "start_date": "2026-09-01",
            "end_date": "2026-12-31",
            "language": "en",
            "evidence_quote": "Get 50,000 points now. Offer valid until 31 December 2026.",
            "confidence": 0.9,
        }
        raw.update(overrides)
        return raw

    def test_requires_title(self):
        self.assertIsNone(clean_campaign(self._raw(campaign_title="   "), self.SOURCE))

    def test_preserves_valid_fields_and_trims_names(self):
        result = clean_campaign(self._raw(product_scope="card_bundle"), self.SOURCE)
        self.assertEqual(result["product_scope"], "card_bundle")
        self.assertEqual(result["card_name"], "Gold Card")
        self.assertEqual(result["campaign_title"], "Gold welcome bonus")
        self.assertEqual(result["campaign_type"], "welcome_bonus")
        self.assertEqual(result["confidence"], 0.9)

    def test_invalid_enums_fall_back_safely(self):
        result = clean_campaign(
            self._raw(product_scope="loan", campaign_type="made_up_type"), self.SOURCE
        )
        self.assertEqual(result["product_scope"], "credit_card")
        self.assertEqual(result["campaign_type"], "other")

    def test_reversed_dates_are_normalized(self):
        result = clean_campaign(
            self._raw(start_date="2026-12-31", end_date="2026-09-01"), self.SOURCE
        )
        self.assertEqual(result["start_date"], "2026-09-01")
        self.assertEqual(result["end_date"], "2026-12-31")

    def test_invalid_dates_become_null(self):
        result = clean_campaign(
            self._raw(start_date="01.09.2026", end_date="2026-02-30"), self.SOURCE
        )
        self.assertIsNone(result["start_date"])
        self.assertIsNone(result["end_date"])

    def test_evidence_quote_accepts_whitespace_and_case_differences(self):
        result = clean_campaign(
            self._raw(evidence_quote="get 50,000 POINTS now.   offer valid until 31 december 2026."),
            self.SOURCE,
        )
        self.assertEqual(result["confidence"], 0.9)

    def test_evidence_quote_accepts_controlled_ellipsis_when_fragments_are_verbatim(self):
        source = (
            "Du bekommst als Neukund:in aktuell bis zu 85.000 Membership Rewards Punkte. "
            "Wichtig: Um den Willkommensbonus von American Express zu erhalten, musst du mit deiner "
            "Platinum Card innerhalb der ersten sechs Monate mindestens 10.000 Euro umsetzen."
        )
        quote = (
            "Du bekommst als Neukund:in aktuell bis zu 85.000 Membership Rewards Punkte. "
            "Wichtig: Um den Willkommensbonus [...] zu erhalten, musst du [...] mindestens 10.000 Euro umsetzen."
        )
        self.assertTrue(evidence_quote_valid(quote, source))

    def test_controlled_ellipsis_cannot_hide_hallucinated_evidence(self):
        source = "Get 2,500 points after your first transaction."
        quote = "Get 2,500 points [...] guaranteed £500 cash reward"
        self.assertFalse(evidence_quote_valid(quote, source))

    def test_missing_or_hallucinated_evidence_caps_confidence(self):
        for quote in (None, "This sentence never appeared on the page"):
            with self.subTest(quote=quote):
                result = clean_campaign(self._raw(evidence_quote=quote, confidence=0.95), self.SOURCE)
                self.assertEqual(result["confidence"], 0.3)

    def test_confidence_is_clamped_and_invalid_value_defaults(self):
        cases = ((1.5, 1.0), (-0.2, 0.0), ("not-a-number", 0.5))
        for raw_confidence, expected in cases:
            with self.subTest(raw_confidence=raw_confidence):
                result = clean_campaign(self._raw(confidence=raw_confidence), self.SOURCE)
                self.assertEqual(result["confidence"], expected)


class BundleDeduplicationTests(unittest.TestCase):
    def test_same_shared_bundle_offer_is_not_emitted_once_per_package_tier(self):
        base = {
            "product_scope": "card_bundle",
            "campaign_type": "welcome_bonus",
            "campaign_title": "Jusqu'à 100 euros offerts",
            "reward_value": "Jusqu'à 100 €",
            "start_date": None,
            "end_date": "2026-12-31",
            "evidence_quote": (
                "Jusqu'à 100 euros offerts aux nouveaux clients souscrivant à une offre "
                "EKO, Essentiel, Premium ou Prestige du Crédit Agricole."
            ),
            "confidence": 0.98,
        }
        campaigns = [{**base, "card_name": name} for name in ("EKO", "Essentiel", "Premium", "Prestige")]
        result = _dedupe_bundle_variants(campaigns)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["card_name"])

    def test_credit_card_variants_are_never_collapsed_by_bundle_guard(self):
        campaigns = [
            {"product_scope": "credit_card", "card_name": "Gold Card"},
            {"product_scope": "credit_card", "card_name": "Gold Rosé Card"},
        ]
        self.assertEqual(len(_dedupe_bundle_variants(campaigns)), 2)


if __name__ == "__main__":
    unittest.main()

