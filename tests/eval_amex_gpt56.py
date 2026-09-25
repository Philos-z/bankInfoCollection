"""Live GPT-5.6 Sol evaluation on the five stored Amex snapshots.

This is intentionally NOT part of unittest discovery: it calls the configured AI proxy.
It never writes to the production database. Run manually after prompt/model changes:

    .venv/bin/python tests/eval_amex_gpt56.py

Exit code 0 means the proxy/model call completed. Quality findings are printed as an
evaluation report rather than treated as deterministic CI assertions.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ai_client
import settings
from extractor.extractor import SYSTEM, _chunks
from extractor.schema import clean_campaign
from validator.rules import card_tokens, reward_numbers


EXPECTED_MODEL = "gpt-5.6-sol"


def _load_snapshots() -> list[dict]:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT r.id, r.text_content_path, s.url, s.source_type,
                      b.name AS bank_name, b.country
               FROM raw_snapshots r
               JOIN sources s ON s.id = r.source_id
               JOIN banks b ON b.id = s.bank_id
              WHERE b.code = 'amex_de'
              ORDER BY r.id
              LIMIT 5"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _extract(snapshot: dict) -> list[dict]:
    text = (settings.ROOT / snapshot["text_content_path"]).read_text(encoding="utf-8")
    header = (
        f"Today: {date.today().isoformat()}\nBank: {snapshot['bank_name']} ({snapshot['country']})\n"
        f"Source URL: {snapshot['url']}\nSource type: {snapshot['source_type']}\n\n"
    )
    campaigns: list[dict] = []
    for i, chunk in enumerate(_chunks(text)):
        result = ai_client.chat_json(SYSTEM, f"{header}Page text (part {i + 1}):\n{chunk}")
        items = result.get("campaigns", []) if isinstance(result, dict) else []
        campaigns.extend(
            c for c in (clean_campaign(x, text) for x in items if isinstance(x, dict)) if c
        )
    return campaigns


def _fingerprint(c: dict) -> tuple:
    """Approximate duplicate signature for evaluation only, not production matching."""
    tokens = tuple(sorted(card_tokens(c.get("card_name"), "American Express Deutschland")))
    rewards = tuple(sorted(reward_numbers(c.get("reward_value"))))
    return c.get("campaign_type"), tokens, rewards


def _report(snapshot: dict, campaigns: list[dict]) -> dict:
    null_cards = sum(not c.get("card_name") for c in campaigns)
    fps = Counter(_fingerprint(c) for c in campaigns)
    duplicate_groups = {str(k): n for k, n in fps.items() if n > 1 and k[1]}
    names = [c.get("card_name") for c in campaigns if c.get("card_name")]
    lower_names = [n.lower() for n in names]
    gold = any("gold" in n and "ros" not in n for n in lower_names)
    rose = any("gold" in n and ("ros" in n or "rosé" in n) for n in lower_names)
    combined_card_names = [
        n for n in names
        if "/" in n or " bzw. " in n.lower() or " or " in n.lower()
    ]
    return {
        "snapshot_id": snapshot["id"],
        "source_type": snapshot["source_type"],
        "url": snapshot["url"],
        "campaign_count": len(campaigns),
        "card_name_null_count": null_cards,
        "card_name_null_rate": round(null_cards / len(campaigns), 3) if campaigns else 0.0,
        "duplicate_signature_groups": duplicate_groups,
        "combined_card_names": combined_card_names,
        "gold_and_rose_both_distinguished": gold and rose,
        "cards": names,
    }


def main() -> None:
    print(f"configured_model={settings.AI_MODEL}")
    if settings.AI_MODEL != EXPECTED_MODEL:
        raise SystemExit(f"Expected {EXPECTED_MODEL}; refusing evaluation with {settings.AI_MODEL}")

    snapshots = _load_snapshots()
    if not snapshots:
        raise SystemExit("No stored Amex snapshots found")

    reports = []
    for snapshot in snapshots:
        campaigns = _extract(snapshot)
        report = _report(snapshot, campaigns)
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))

    total = sum(r["campaign_count"] for r in reports)
    nulls = sum(r["card_name_null_count"] for r in reports)
    summary = {
        "model": settings.AI_MODEL,
        "snapshots": len(reports),
        "campaigns": total,
        "card_name_null_count": nulls,
        "card_name_null_rate": round(nulls / total, 3) if total else 0.0,
        "snapshots_with_duplicate_signatures": sum(bool(r["duplicate_signature_groups"]) for r in reports),
        "combined_card_name_count": sum(len(r["combined_card_names"]) for r in reports),
        "snapshots_distinguishing_gold_and_rose": sum(r["gold_and_rose_both_distinguished"] for r in reports),
    }
    print("\nSUMMARY")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
