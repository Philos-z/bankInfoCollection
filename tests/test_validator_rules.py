"""Regression tests for campaign verification and normalization rules.

Includes real BBVA and Crédit Agricole cases documented in sys.md.
See tests/README.md for failure interpretation and maintenance rules.
"""

import unittest
from datetime import date

from validator import rules


def supporter(
    domain,
    *,
    end_date="2026-12-31",
    reward_value="100 EUR",
    confidence=0.9,
    source_type="third_party",
    source_role="primary",
    extracted_at="2026-09-23T10:00:00+00:00",
):
    return {
        "domain": domain,
        "end_date": end_date,
        "reward_value": reward_value,
        "confidence": confidence,
        "source_type": source_type,
        "source_role": source_role,
        "extracted_at": extracted_at,
    }


class RewardNumberTests(unittest.TestCase):
    def test_parses_european_us_and_space_thousands_separators(self):
        for value in ("85,000 points", "85.000 Punkte", "85 000 points", "85\u00a0000 points"):
            with self.subTest(value=value):
                self.assertIn(85000.0, rules.reward_numbers(value))

    def test_parses_decimal_percentage(self):
        self.assertIn(5.5, rules.reward_numbers("5.5% cashback"))

    def test_number_relation_matches_on_shared_number(self):
        self.assertEqual(rules.number_relation("up to 85,000 points", "85.000 Punkte"), "match")
        self.assertEqual(rules.number_relation("50,000 points", "85,000 points"), "mismatch")
        self.assertEqual(rules.number_relation(None, "85,000 points"), "unknown")


class OfferMechanicTests(unittest.TestCase):
    def test_balance_transfer_mechanics_match_despite_different_titles(self):
        a = {
            "campaign_title": "Definite 36mths 0%",
            "offer_summary": "Interest-free balance transfer for 36 months",
            "conditions": "3.09% balance transfer fee",
        }
        b = {
            "campaign_title": "0% interest on balance transfers",
            "offer_summary": "0% balance transfer offer",
            "conditions": None,
        }
        self.assertEqual(rules.mechanic_relation(a, b), "match")

    def test_purchase_and_balance_transfer_mechanics_do_not_match(self):
        a = {"campaign_title": "0% purchases", "offer_summary": "purchase offer", "conditions": None}
        b = {"campaign_title": "0% balance transfer", "offer_summary": "balance transfer offer", "conditions": None}
        self.assertEqual(rules.mechanic_relation(a, b), "mismatch")


class CardMatchingTests(unittest.TestCase):
    def _row(self, card_name, title="Welcome bonus", reward="50,000 points"):
        return {
            "bank_id": 1,
            "bank_name": "American Express Deutschland",
            "card_name": card_name,
            "campaign_title": title,
            "campaign_type": "welcome_bonus",
            "reward_value": reward,
            "end_date": None,
        }

    def test_bank_and_generic_words_do_not_change_card_identity(self):
        a = self._row("Gold Card")
        b = self._row("American Express Gold Credit Card")
        self.assertEqual(rules.card_relation(a, b), "same")

    def test_gold_and_gold_rose_are_not_exact_card_matches(self):
        a = self._row("Gold Card")
        b = self._row("Gold Rosé Card")
        self.assertEqual(rules.card_relation(a, b), "similar")
        self.assertNotEqual(rules.card_relation(a, b), "same")

    def test_marketing_expanded_name_is_similar_not_exact_without_product_specific_alias(self):
        a = self._row("BMW Premium Card Carbon")
        b = self._row("BMW Card Carbon")
        self.assertEqual(rules.card_relation(a, b), "similar")

    def test_different_cards_are_rejected_before_title_similarity(self):
        a = self._row("Platinum Card")
        b = self._row("Green Card")
        self.assertEqual(rules.card_relation(a, b), "different")
        self.assertEqual(rules.similarity(a, b), 0.0)

    def test_different_bank_or_campaign_type_cannot_match(self):
        a = self._row("Gold Card")
        b = self._row("Gold Card")
        b["bank_id"] = 2
        self.assertEqual(rules.similarity(a, b), 0.0)
        b["bank_id"] = 1
        b["campaign_type"] = "cashback"
        self.assertEqual(rules.similarity(a, b), 0.0)


class VerificationTests(unittest.TestCase):
    def test_credit_agricole_regression_three_domains_is_verified(self):
        supporters = [
            supporter("credit-agricole.fr", source_type="official"),
            supporter("selectra.info"),
            supporter("moneyvox.fr"),
        ]
        status, notes = rules.verify(supporters)
        self.assertEqual(status, "verified")
        self.assertIn("3 independent domain(s)", notes)

    def test_same_domain_does_not_count_twice(self):
        status, notes = rules.verify([
            supporter("example.com"),
            supporter("example.com", extracted_at="2026-09-23T11:00:00+00:00"),
        ])
        self.assertEqual(status, "unverified")
        self.assertIn("1 independent domain(s)", notes)

    def test_low_confidence_source_does_not_confirm(self):
        status, notes = rules.verify([
            supporter("official.example", source_type="official"),
            supporter("weak.example", confidence=0.3),
        ])
        self.assertEqual(status, "unverified")
        self.assertIn("1 independent domain(s)", notes)

    def test_bbva_regression_different_end_dates_is_conflict(self):
        status, notes = rules.verify([
            supporter("bbva.es", end_date="2026-10-20", reward_value="1,060 EUR", source_type="official"),
            supporter("eleconomista.es", end_date="2026-07-22", reward_value="1.060 EUR"),
        ])
        self.assertEqual(status, "conflict")
        self.assertIn("end_date disagreement", notes)

    def test_reward_disagreement_is_conflict(self):
        status, notes = rules.verify([
            supporter("official.example", reward_value="50,000 points", source_type="official"),
            supporter("review.example", reward_value="85,000 points"),
        ])
        self.assertEqual(status, "conflict")
        self.assertIn("reward disagreement", notes)

    def test_two_third_party_domains_can_verify_under_current_semantics(self):
        status, _ = rules.verify([
            supporter("review-a.example"),
            supporter("review-b.example"),
        ])
        self.assertEqual(status, "verified")


class LifecycleTests(unittest.TestCase):
    TODAY = date(2026, 9, 23)

    def test_upcoming(self):
        self.assertEqual(rules.lifecycle("2026-09-24", "2026-10-31", self.TODAY), "upcoming")

    def test_expired(self):
        self.assertEqual(rules.lifecycle(None, "2026-09-22", self.TODAY), "expired")

    def test_expiring_includes_today_and_fourteen_day_boundary(self):
        self.assertEqual(rules.lifecycle(None, "2026-09-23", self.TODAY), "expiring")
        self.assertEqual(rules.lifecycle(None, "2026-10-07", self.TODAY), "expiring")

    def test_active_beyond_expiring_window_or_without_end_date(self):
        self.assertEqual(rules.lifecycle(None, "2026-10-08", self.TODAY), "active")
        self.assertEqual(rules.lifecycle(None, None, self.TODAY), "active")


class PrimarySourceTests(unittest.TestCase):
    def test_official_source_wins_even_with_lower_confidence(self):
        third_party = supporter("review.example", confidence=0.99)
        official = supporter("bank.example", confidence=0.6, source_type="official")
        self.assertIs(rules.pick_primary([third_party, official]), official)

    def test_highest_confidence_wins_within_same_source_type(self):
        low = supporter("a.example", confidence=0.7)
        high = supporter("b.example", confidence=0.9)
        self.assertIs(rules.pick_primary([low, high]), high)

    def test_primary_role_beats_supporting_official_source(self):
        primary_third_party = supporter(
            "review.example", confidence=0.8, source_type="third_party", source_role="primary"
        )
        supporting_official = supporter(
            "bank.example", confidence=0.99, source_type="official", source_role="supporting"
        )
        self.assertIs(rules.pick_primary([supporting_official, primary_third_party]), primary_third_party)

    def test_low_confidence_official_does_not_become_primary_when_solid_evidence_exists(self):
        shaky_official = supporter("bank.example", confidence=0.3, source_type="official")
        solid_third_party = supporter("review.example", confidence=0.9, source_type="third_party")
        self.assertIs(rules.pick_primary([shaky_official, solid_third_party]), solid_third_party)


if __name__ == "__main__":
    unittest.main()

