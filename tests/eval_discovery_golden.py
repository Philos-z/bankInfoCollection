"""Evaluate persisted discovery recommendations against manually reviewed golden cases.

This script does not call AI or the network. It reads the current SQLite results and
the human-reviewed fixture in tests/fixtures/discovery_golden.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings


def main() -> None:
    golden = json.loads((ROOT / "tests/fixtures/discovery_golden.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT found_url, validation_status, recommended_role FROM discovery_leads"
    )]
    conn.close()

    results = []
    for case in golden:
        matches = [r for r in rows if case["contains"] in r["found_url"]]
        if len(matches) != 1:
            results.append({**case, "actual_status": "missing_or_ambiguous", "actual_role": None, "ok": False})
            continue
        row = matches[0]
        ok = row["validation_status"] == case["expected_status"] and row["recommended_role"] == case["expected_role"]
        results.append({
            **case,
            "actual_status": row["validation_status"],
            "actual_role": row["recommended_role"],
            "ok": ok,
        })

    expected_positive = [r for r in results if r["expected_status"] == "recommended"]
    predicted_positive = [r for r in results if r["actual_status"] == "recommended"]
    true_positive = [r for r in predicted_positive if r["expected_status"] == "recommended"]
    exact = [r for r in results if r["ok"]]
    precision = len(true_positive) / len(predicted_positive) if predicted_positive else 0.0
    recall = len(true_positive) / len(expected_positive) if expected_positive else 0.0
    exact_accuracy = len(exact) / len(results) if results else 0.0

    report = {
        "cases": len(results),
        "exact_status_role_accuracy": round(exact_accuracy, 4),
        "recommendation_precision": round(precision, 4),
        "recommendation_recall": round(recall, 4),
        "mismatches": [r for r in results if not r["ok"]],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
