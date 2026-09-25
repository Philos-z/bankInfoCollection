"""Offline tests for discovery stage-2 content validation policy."""

import unittest

from crawler import content_validator as cv
from crawler.ai_discovery import canonicalize_url


class UrlPrefilterTests(unittest.TestCase):
    def test_rejects_application_flow(self):
        result = cv.prefilter_url("https://dco-cc.hsbc.co.uk/ntb?offerId=ABC")
        self.assertEqual(result["validation_status"], "reject")
        self.assertEqual(result["page_type"], "application")

    def test_rejects_generic_agb(self):
        result = cv.prefilter_url("https://bank.example/agb")
        self.assertEqual(result["validation_status"], "reject")
        self.assertEqual(result["page_type"], "legal")

    def test_does_not_prefilter_offer_terms(self):
        self.assertIsNone(cv.prefilter_url("https://bank.example/card/offer-conditions"))

    def test_canonical_url_removes_tracking_but_keeps_offer_selection(self):
        url = "https://BANK.example/card?offerId=ABC&inav=menu&utm_source=x&productCode=42"
        self.assertEqual(
            canonicalize_url(url),
            "https://bank.example/card?offerId=ABC&productCode=42",
        )

    def test_tracking_variants_have_same_canonical_identity(self):
        a = "https://bank.example/card?inav=menu&utm_source=x&offerId=ABC"
        b = "https://bank.example/card?offerId=ABC&linknav=other"
        self.assertEqual(canonicalize_url(a), canonicalize_url(b))


class AiResultCleaningTests(unittest.TestCase):
    TEXT = "Get 2,500 points as a welcome bonus when you make your first transaction."

    def _raw(self, **overrides):
        raw = {
            "page_type": "product_with_current_offer",
            "has_concrete_offer": True,
            "offer_type": "welcome_bonus",
            "offer_summary": "2,500 point welcome bonus",
            "has_eligibility": True,
            "has_time_or_intro_period": False,
            "is_stable_evidence_page": True,
            "is_standing_feature": False,
            "recommended_role": "primary",
            "evidence_quote": "Get 2,500 points as a welcome bonus when you make your first transaction.",
            "confidence": 0.95,
            "reason": "Concrete welcome offer on a stable product page",
        }
        raw.update(overrides)
        return raw

    def test_valid_quote_preserves_confidence(self):
        result = cv.clean_ai_result(self._raw(), self.TEXT)
        self.assertTrue(result["quote_valid"])
        self.assertEqual(result["validation_confidence"], 0.95)

    def test_hallucinated_quote_caps_confidence(self):
        result = cv.clean_ai_result(self._raw(evidence_quote="Not on the page"), self.TEXT)
        self.assertFalse(result["quote_valid"])
        self.assertEqual(result["validation_confidence"], 0.3)

    def test_pdf_spacing_artifact_still_validates_long_quote(self):
        source = "Pur chases 0% on purchases for up to 24 months from a ccount opening"
        quote = "Purchases 0% on purchases for up to 24 months from account opening"
        self.assertTrue(cv.evidence_quote_valid(quote, source))

    def test_inline_footnote_marker_does_not_break_verbatim_evidence(self):
        source = "receive 70,000 reward points when you spend[@eligible-spend-excludes] £2,000 in the first 90 days"
        quote = "receive 70,000 reward points when you spend £2,000 in the first 90 days"
        self.assertTrue(cv.evidence_quote_valid(quote, source))

    def test_standalone_numeric_footnote_lines_do_not_break_evidence(self):
        source = "0% interest for up to 36 months\n1\non balance transfers made within 60 days\n2\n. A 3.09% fee applies"
        quote = "0% interest for up to 36 months on balance transfers made within 60 days. A 3.09% fee applies"
        self.assertTrue(cv.evidence_quote_valid(quote, source))

    def test_inline_marker_in_quote_is_ignored_too(self):
        source = "receive 70,000 reward points when you spend £2,000 in the first 90 days"
        quote = "receive 70,000 reward points when you spend[@eligible-spend-excludes] £2,000 in the first 90 days"
        self.assertTrue(cv.evidence_quote_valid(quote, source))

    def test_explicit_ellipsis_is_allowed_only_when_fragments_exist_in_order(self):
        source = (
            "Nach Belastungen mit deiner Business Platinum Card Hauptkarte von mindestens 15.000 Euro "
            "und weiteren Bedingungen innerhalb der ersten 6 Monate nach Kartenerhalt bekommst du "
            "eine Gutschrift in Höhe von 200.000 Membership Rewards Punkten"
        )
        quote = (
            "Nach Belastungen mit deiner Business Platinum Card Hauptkarte von mindestens 15.000 Euro "
            "[...] innerhalb der ersten 6 Monate nach Kartenerhalt [...] bekommst du eine Gutschrift "
            "in Höhe von 200.000 Membership Rewards Punkten"
        )
        self.assertTrue(cv.evidence_quote_valid(quote, source))

    def test_ellipsis_cannot_hide_hallucinated_fragment(self):
        source = "Get 2,500 points as a welcome bonus after your first transaction."
        quote = "Get 2,500 points [...] guaranteed £500 cash reward"
        self.assertFalse(cv.evidence_quote_valid(quote, source))

    def test_invalid_enum_falls_back(self):
        result = cv.clean_ai_result(self._raw(page_type="magic", offer_type="magic"), self.TEXT)
        self.assertEqual(result["page_type"], "other")
        self.assertEqual(result["offer_type"], "other")


class RecommendationPolicyTests(unittest.TestCase):
    def _result(self, **overrides):
        result = {
            "page_type": "campaign",
            "has_concrete_offer": True,
            "offer_type": "welcome_bonus",
            "has_eligibility": True,
            "has_time_or_intro_period": True,
            "is_stable_evidence_page": True,
            "is_standing_feature": False,
            "recommended_role": "primary",
            "quote_valid": True,
            "validation_confidence": 0.95,
        }
        result.update(overrides)
        return result

    def test_concrete_campaign_is_recommended_primary(self):
        result = cv.apply_policy(self._result())
        self.assertEqual(result["validation_status"], "recommended")
        self.assertEqual(result["recommended_role"], "primary")
        self.assertGreaterEqual(result["content_score"], 0.7)

    def test_campaign_terms_are_recommended_supporting(self):
        result = cv.apply_policy(self._result(page_type="campaign_terms", recommended_role="supporting"))
        self.assertEqual(result["validation_status"], "recommended")
        self.assertEqual(result["recommended_role"], "supporting")

    def test_educational_page_is_rejected_even_with_offer_words(self):
        result = cv.apply_policy(self._result(page_type="educational"))
        self.assertEqual(result["validation_status"], "reject")
        self.assertIsNone(result["recommended_role"])

    def test_personalized_offer_hub_requires_review(self):
        result = cv.apply_policy(self._result(page_type="personalized_offer_hub"))
        self.assertEqual(result["validation_status"], "review")

    def test_generic_product_without_concrete_offer_is_rejected(self):
        result = cv.apply_policy(self._result(
            page_type="generic_product",
            has_concrete_offer=False,
            has_eligibility=False,
            has_time_or_intro_period=False,
            quote_valid=False,
            validation_confidence=0.9,
        ))
        self.assertEqual(result["validation_status"], "reject")

    def test_standing_feature_is_rejected_even_if_concrete_and_recurring(self):
        result = cv.apply_policy(self._result(
            page_type="campaign_terms",
            offer_type="points_multiplier",
            has_concrete_offer=True,
            has_time_or_intro_period=True,
            is_stable_evidence_page=True,
            is_standing_feature=True,
            recommended_role="supporting",
        ))
        self.assertEqual(result["validation_status"], "reject")
        self.assertIsNone(result["recommended_role"])

    def test_invalid_evidence_cannot_be_recommended(self):
        result = cv.apply_policy(self._result(
            page_type="campaign",
            has_concrete_offer=True,
            quote_valid=False,
            validation_confidence=0.3,
        ))
        self.assertEqual(result["validation_status"], "review")
        self.assertIsNone(result["recommended_role"])

    def test_welcome_bonus_overrides_erroneous_standing_feature_label(self):
        result = cv.apply_policy(self._result(
            page_type="product_with_current_offer",
            offer_type="welcome_bonus",
            has_concrete_offer=True,
            is_standing_feature=True,
        ))
        self.assertEqual(result["validation_status"], "recommended")
        self.assertFalse(result["is_standing_feature"])


class ExcerptTests(unittest.TestCase):
    def test_long_page_keeps_relevant_offer_section(self):
        text = "x" * 30000 + "\nGet 2,500 points as a welcome bonus.\n" + "y" * 30000
        excerpt = cv.validation_excerpt(text)
        self.assertIn("welcome bonus", excerpt)
        self.assertLessEqual(len(excerpt), 24000)


if __name__ == "__main__":
    unittest.main()

