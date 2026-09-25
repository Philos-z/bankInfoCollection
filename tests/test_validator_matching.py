"""Regression tests for campaign entity matching boundaries.

The Amex Gold / Gold Rosé case protects against accidental campaign merging.
See tests/README.md for the business rationale.
"""

import unittest
from unittest.mock import patch

from validator.validate import _match


def row(*, snapshot_id=1, card_name="Gold Card"):
    return {
        "id": 10,
        "snapshot_id": snapshot_id,
        "bank_id": 1,
        "bank_name": "American Express Deutschland",
        "card_name": card_name,
        "campaign_title": "50,000 Membership Rewards welcome bonus",
        "campaign_type": "welcome_bonus",
        "reward_value": "50,000 points",
        "end_date": None,
    }


class MatchRegressionTests(unittest.TestCase):
    def test_same_snapshot_gold_and_rose_are_not_rule_merged(self):
        extraction = row(snapshot_id=77, card_name="Gold Rosé Card")
        existing = row(snapshot_id=77, card_name="Gold Card")
        existing["id"] = 123

        with patch("validator.validate.ai_arbiter.same_campaign", return_value=False) as arbiter:
            campaign, method = _match(extraction, [existing], {123: {77}})

        self.assertIsNone(campaign)
        self.assertEqual(method, "")
        arbiter.assert_called_once()

    def test_same_snapshot_exact_normalized_card_can_be_deduplicated(self):
        extraction = row(snapshot_id=77, card_name="American Express Blue Card")
        extraction["campaign_title"] = "25 Euro Startguthaben"
        extraction["reward_value"] = "25 EUR"
        existing = row(snapshot_id=77, card_name="Blue Card")
        existing["id"] = 123
        existing["campaign_title"] = "Blue Card Startguthaben"
        existing["reward_value"] = "€25 statement credit"

        with patch("validator.validate.ai_arbiter.same_campaign") as arbiter:
            campaign, method = _match(extraction, [existing], {123: {77}})

        self.assertEqual(campaign["id"], 123)
        self.assertEqual(method, "rule")
        arbiter.assert_not_called()

    def test_missing_card_name_is_sent_to_ai_when_offer_evidence_matches(self):
        extraction = row(snapshot_id=2, card_name=None)
        extraction["campaign_title"] = "Bis zu 50.000 Membership Rewards Punkte"
        existing = row(snapshot_id=1, card_name="American Express Gold Card")
        existing["id"] = 123

        with patch("validator.validate.ai_arbiter.same_campaign", return_value=True) as arbiter:
            campaign, method = _match(extraction, [existing], {123: {1}})

        self.assertEqual(campaign["id"], 123)
        self.assertEqual(method, "ai")
        arbiter.assert_called_once()

    def test_same_snapshot_marketing_expanded_alias_requires_ai(self):
        extraction = row(snapshot_id=77, card_name="BMW Card Carbon")
        extraction["campaign_title"] = "37.500 Membership Rewards Punkte Willkommensbonus"
        extraction["reward_value"] = "37,500 Membership Rewards points"
        existing = row(snapshot_id=77, card_name="BMW Premium Card Carbon")
        existing["id"] = 123
        existing["campaign_title"] = "BMW Premium Card Carbon Willkommensbonus"
        existing["reward_value"] = "37,500 Membership Rewards points"

        with patch("validator.validate.ai_arbiter.same_campaign", return_value=True) as arbiter:
            campaign, method = _match(extraction, [existing], {123: {77}})

        self.assertEqual(campaign["id"], 123)
        self.assertEqual(method, "ai")
        arbiter.assert_called_once()

    def test_exact_card_high_score_uses_rule_without_ai(self):
        extraction = row(snapshot_id=2)
        existing = row(snapshot_id=1)
        existing["id"] = 123

        with patch("validator.validate.ai_arbiter.same_campaign") as arbiter:
            campaign, method = _match(extraction, [existing], {123: {1}})

        self.assertEqual(campaign["id"], 123)
        self.assertEqual(method, "rule")
        arbiter.assert_not_called()

    def test_card_variant_requires_ai_even_when_offer_is_identical(self):
        extraction = row(snapshot_id=2, card_name="Gold Rosé Card")
        existing = row(snapshot_id=1, card_name="Gold Card")
        existing["id"] = 123

        with patch("validator.validate.ai_arbiter.same_campaign", return_value=False) as arbiter:
            campaign, method = _match(extraction, [existing], {123: {1}})

        self.assertIsNone(campaign)
        self.assertEqual(method, "")
        arbiter.assert_called_once()

    def test_missing_card_name_rule_matches_unique_balance_transfer_mechanics(self):
        extraction = row(snapshot_id=2, card_name=None)
        extraction.update({
            "bank_id": 2,
            "bank_name": "HSBC UK",
            "campaign_title": "Definite 36mths 0%",
            "campaign_type": "interest_free",
            "reward_value": "0% for 36 months",
            "offer_summary": "HSBC offers an interest-free balance transfer period of 36 months.",
            "conditions": "A 3.09% balance transfer fee applies.",
        })
        existing = row(snapshot_id=1, card_name="Balance Transfer Credit Card")
        existing.update({
            "id": 123,
            "bank_id": 2,
            "bank_name": "HSBC UK",
            "campaign_title": "0% interest on balance transfers",
            "campaign_type": "interest_free",
            "reward_value": "0% for up to 36 months",
            "offer_summary": "0% balance transfers for up to 36 months.",
            "conditions": "Balance transfers must be made within 60 days.",
        })

        with patch("validator.validate.ai_arbiter.same_campaign") as arbiter:
            campaign, method = _match(extraction, [existing], {123: {1}})

        self.assertEqual(campaign["id"], 123)
        self.assertEqual(method, "rule")
        arbiter.assert_not_called()

    def test_card_bundle_same_reward_matches_unique_umbrella_campaign(self):
        extraction = row(snapshot_id=2, card_name=None)
        extraction.update({
            "bank_id": 5,
            "bank_name": "Crédit Agricole",
            "product_scope": "card_bundle",
            "campaign_title": "Jusqu'à 100 euros offerts",
            "campaign_type": "welcome_bonus",
            "reward_value": "Jusqu'à 100 €",
            "end_date": "2026-12-31",
        })
        existing = row(snapshot_id=1, card_name=None)
        existing.update({
            "id": 222,
            "bank_id": 5,
            "bank_name": "Crédit Agricole",
            "product_scope": "card_bundle",
            "campaign_title": "Jusqu'à 100€ de bienvenue",
            "campaign_type": "welcome_bonus",
            "reward_value": "Up to €100",
            "end_date": None,
        })

        with patch("validator.validate.ai_arbiter.same_campaign") as arbiter:
            campaign, method = _match(extraction, [existing], {222: {1}})

        self.assertEqual(campaign["id"], 222)
        self.assertEqual(method, "rule")
        arbiter.assert_not_called()

    def test_same_snapshot_bundle_components_with_same_amount_stay_separate(self):
        extraction = row(snapshot_id=77, card_name=None)
        extraction.update({
            "bank_id": 5,
            "bank_name": "Crédit Agricole",
            "product_scope": "card_bundle",
            "campaign_title": "50€ offerts pour la mobilité bancaire",
            "campaign_type": "welcome_bonus",
            "reward_value": "€50",
            "end_date": "2026-12-31",
        })
        existing = row(snapshot_id=77, card_name=None)
        existing.update({
            "id": 333,
            "bank_id": 5,
            "bank_name": "Crédit Agricole",
            "product_scope": "card_bundle",
            "campaign_title": "50€ offerts à l’ouverture d’un compte",
            "campaign_type": "welcome_bonus",
            "reward_value": "€50",
            "end_date": "2026-12-31",
        })

        with patch("validator.validate.ai_arbiter.same_campaign") as arbiter:
            campaign, method = _match(extraction, [existing], {333: {77}})

        self.assertIsNone(campaign)
        self.assertEqual(method, "")
        arbiter.assert_not_called()


if __name__ == "__main__":
    unittest.main()

