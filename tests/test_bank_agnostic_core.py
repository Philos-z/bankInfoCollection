"""Guardrail: core production logic must not encode bank/product-specific business rules.

Real bank/product names belong in configuration and regression fixtures, not in the generic
extraction, matching, verification, discovery or health engines.
"""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = [
    ROOT / "main.py",
    ROOT / "extractor/extractor.py",
    ROOT / "extractor/schema.py",
    ROOT / "validator/rules.py",
    ROOT / "validator/validate.py",
    ROOT / "validator/ai_arbiter.py",
    ROOT / "crawler/ai_discovery.py",
    ROOT / "crawler/content_validator.py",
    ROOT / "crawler/source_health.py",
]

# Concrete entities seen in current regression data. Add future concrete bank/product names here
# when they expose a new bug; production logic should still remain entity-agnostic.
FORBIDDEN_CORE_LITERALS = (
    "American Express",
    "Amex",
    "BMW",
    "Gold Rosé",
    "PAYBACK",
    "HSBC",
    "BBVA",
    "Crédit Agricole",
    "EKO",
    "Essentiel",
    "Moneyvox",
    "MoneySavingExpert",
)


class BankAgnosticCoreTests(unittest.TestCase):
    def test_core_logic_contains_no_concrete_bank_or_product_rules(self):
        violations = []
        for path in CORE_FILES:
            text = path.read_text(encoding="utf-8")
            for literal in FORBIDDEN_CORE_LITERALS:
                if literal.lower() in text.lower():
                    violations.append(f"{path.relative_to(ROOT)}: {literal}")
        self.assertEqual(violations, [], "Concrete bank/product knowledge leaked into core: " + "; ".join(violations))


if __name__ == "__main__":
    unittest.main()
