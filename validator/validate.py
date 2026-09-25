import logging
from pathlib import Path

import settings
from extractor.schema import evidence_quote_valid
from storage.db import now_iso, session
from validator import ai_arbiter, rules

log = logging.getLogger(__name__)

# Extractions from the most recent extracted snapshot of every source = what the web says *now*.
CURRENT_EXTRACTIONS = """
SELECT e.*, s.domain, s.source_type, s.source_role, cd.name AS card_name, b.name AS bank_name
FROM campaign_extractions e
JOIN raw_snapshots r ON r.id = e.snapshot_id
JOIN sources s ON s.id = r.source_id
JOIN banks b ON b.id = e.bank_id
LEFT JOIN cards cd ON cd.id = e.card_id
WHERE r.id = (SELECT r2.id FROM raw_snapshots r2
              WHERE r2.source_id = r.source_id AND r2.extracted = 1
              ORDER BY r2.fetched_at DESC, r2.id DESC LIMIT 1)
"""

CAMPAIGNS = """
SELECT c.*, c.canonical_title AS campaign_title, cd.name AS card_name, b.name AS bank_name
FROM campaigns c JOIN banks b ON b.id = c.bank_id LEFT JOIN cards cd ON cd.id = c.card_id
WHERE c.lifecycle_status != 'removed'
"""


def _match(extraction: dict, campaigns: list[dict], snapshots_of: dict[int, set[int]]) -> tuple[dict | None, str]:
    candidates = []
    for c in campaigns:
        same_snapshot = extraction["snapshot_id"] in snapshots_of.get(c["id"], set())
        score = rules.similarity(extraction, c)
        if score >= rules.AMBIGUOUS_THRESHOLD:
            candidates.append((score, same_snapshot, c))
    candidates.sort(key=lambda x: x[0], reverse=True)

    # When exactly one candidate has the same concrete offer mechanics and reward, a missing
    # card name should not create a duplicate campaign. This covers comparison-site rows such
    # as "36mths 0%" where the summary clearly says balance transfer but the product label is absent.
    inferred = []
    for score, _same_snapshot, c in candidates:
        one_card_missing = bool(extraction.get("card_name")) != bool(c.get("card_name"))
        if (
            one_card_missing
            and score >= 0.60
            and rules.number_relation(extraction.get("reward_value"), c.get("reward_value")) == "match"
            and rules.mechanic_relation(extraction, c) == "match"
        ):
            inferred.append((score, c))
    if len(inferred) == 1:
        return inferred[0][1], "rule"

    # Card-bundle campaigns often apply to several account/package tiers while the canonical
    # campaign intentionally has no single card name. If exactly one bundle candidate has the
    # same reward and a strong overall similarity, treat it as the same umbrella campaign.
    bundle_inferred = []
    for score, _same_snapshot, c in candidates:
        if (
            extraction.get("product_scope") == "card_bundle"
            and c.get("product_scope") == "card_bundle"
            and not _same_snapshot
            and score >= 0.60
            and rules.card_relation(extraction, c) != "different"
            and rules.number_relation(extraction.get("reward_value"), c.get("reward_value")) == "match"
        ):
            bundle_inferred.append((score, c))
    if len(bundle_inferred) == 1:
        return bundle_inferred[0][1], "rule"

    for score, same_snapshot, c in candidates[:3]:
        relation = rules.card_relation(extraction, c)
        # Exact normalized product identity + strong offer similarity is safe even when a page repeated
        # the same promotion using an issuer-prefixed and a shortened product label.
        if score >= rules.SAME_THRESHOLD and relation == "same":
            return c, "rule"
        # After extraction-level bundle deduplication, multiple remaining card-bundle rows on
        # the same page represent distinct components/offers (e.g. €50 opening + €50 bank switch).
        if (
            same_snapshot
            and extraction.get("product_scope") == "card_bundle"
            and c.get("product_scope") == "card_bundle"
        ):
            continue
        # Same-page non-exact candidates are deliberately never rule-merged. They may be marketing-expanded
        # versus shortened labels for one product, but can also be genuine colour/edition/tier variants,
        # so require the conservative AI arbiter.
        if ai_arbiter.same_campaign(extraction, c):
            return c, "ai"
    return None, ""


def _create_campaign(conn, e: dict) -> dict:
    ts = now_iso()
    cur = conn.execute(
        """INSERT INTO campaigns (bank_id, card_id, product_scope, canonical_title, campaign_type, offer_summary,
               reward_value, conditions, start_date, end_date, primary_extraction_id,
               first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (e["bank_id"], e["card_id"], e["product_scope"], e["campaign_title"], e["campaign_type"], e["offer_summary"],
         e["reward_value"], e["conditions"], e["start_date"], e["end_date"], e["id"], ts, ts),
    )
    return {**e, "id": cur.lastrowid}


def _campaign_evidence_still_present(conn, campaign_id: int) -> bool:
    """Guard lifecycle against transient LLM extraction misses.

    A campaign should not become `removed` merely because the latest extraction pass failed to emit it.
    If evidence from a previously linked extraction is still grounded in the latest extracted snapshot of
    an enabled source, the campaign is still visibly present on the web page.
    """
    rows = conn.execute(
        """SELECT DISTINCT e.evidence_quote, r.source_id
             FROM campaign_sources cs
             JOIN campaign_extractions e ON e.id = cs.extraction_id
             JOIN raw_snapshots r ON r.id = e.snapshot_id
             JOIN sources s ON s.id = r.source_id
            WHERE cs.campaign_id = ?
              AND s.enabled = 1
              AND e.evidence_quote IS NOT NULL
              AND TRIM(e.evidence_quote) != ''""",
        (campaign_id,),
    ).fetchall()
    for row in rows:
        latest = conn.execute(
            """SELECT text_content_path FROM raw_snapshots
                WHERE source_id = ? AND extracted = 1
                ORDER BY fetched_at DESC, id DESC LIMIT 1""",
            (row["source_id"],),
        ).fetchone()
        if not latest:
            continue
        path = settings.ROOT / latest["text_content_path"]
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if evidence_quote_valid(row["evidence_quote"], text):
            return True
    return False


def _refresh_campaign(conn, campaign_id: int, supporters: list[dict]) -> None:
    ts = now_iso()
    if not supporters:
        existing = conn.execute(
            "SELECT start_date, end_date, lifecycle_status FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        lifecycle = rules.lifecycle(existing["start_date"], existing["end_date"])
        if lifecycle == "expired":
            conn.execute("UPDATE campaigns SET lifecycle_status = 'expired' WHERE id = ?", (campaign_id,))
        elif _campaign_evidence_still_present(conn, campaign_id):
            conn.execute(
                "UPDATE campaigns SET lifecycle_status = ?, last_seen_at = ? WHERE id = ?",
                (lifecycle, ts, campaign_id),
            )
        else:
            conn.execute("UPDATE campaigns SET lifecycle_status = 'removed' WHERE id = ?", (campaign_id,))
        return
    primary = rules.pick_primary(supporters)
    status, notes = rules.verify(supporters)
    start = primary["start_date"] or next((s["start_date"] for s in supporters if s["start_date"]), None)
    end = primary["end_date"] or next((s["end_date"] for s in supporters if s["end_date"]), None)
    conn.execute(
        """UPDATE campaigns SET card_id = COALESCE(?, card_id), product_scope = ?, canonical_title = ?, offer_summary = ?,
               reward_value = ?, conditions = ?, start_date = ?, end_date = ?,
               verification_status = ?, verification_notes = ?, lifecycle_status = ?,
               primary_extraction_id = ?, last_seen_at = ?,
               last_verified_at = CASE WHEN ? = 'verified' THEN ? ELSE last_verified_at END
           WHERE id = ?""",
        (primary["card_id"], primary["product_scope"], primary["campaign_title"], primary["offer_summary"], primary["reward_value"],
         primary["conditions"], start, end, status, notes, rules.lifecycle(start, end),
         primary["id"], ts, status, ts, campaign_id),
    )


def validate(bank_code: str | None = None, rebuild: bool = False) -> dict:
    stats = {"linked": 0, "created": 0, "ai_matches": 0, "supporting_unmatched": 0}
    with session() as conn:
        bank_id = None
        if bank_code:
            bank_id = conn.execute("SELECT id FROM banks WHERE code = ?", (bank_code,)).fetchone()["id"]

        if rebuild:
            # Campaigns are derived from immutable extractions, so they can be regenerated after rule changes.
            scope, params = ("WHERE bank_id = ?", (bank_id,)) if bank_id else ("", ())
            conn.execute(f"DELETE FROM campaign_sources WHERE campaign_id IN (SELECT id FROM campaigns {scope})", params)
            conn.execute(f"DELETE FROM campaigns {scope}", params)

        current = [dict(r) for r in conn.execute(
            CURRENT_EXTRACTIONS + (" AND e.bank_id = ?" if bank_id else ""), (bank_id,) if bank_id else ())]
        campaigns = [dict(c) for c in conn.execute(
            CAMPAIGNS + (" AND c.bank_id = ?" if bank_id else ""), (bank_id,) if bank_id else ())]

        snapshots_of: dict[int, set[int]] = {}
        linked_ids = set()
        for r in conn.execute(
            """SELECT cs.campaign_id, cs.extraction_id, e.snapshot_id FROM campaign_sources cs
               JOIN campaign_extractions e ON e.id = cs.extraction_id"""):
            snapshots_of.setdefault(r["campaign_id"], set()).add(r["snapshot_id"])
            linked_ids.add(r["extraction_id"])

        # Primary sources seed campaigns. Supporting sources are processed afterwards and may only
        # attach evidence to an existing campaign; they never create a new campaign on their own.
        for e in sorted(current, key=lambda x: (
            x.get("source_role", "primary") != "primary",
            x["source_type"] != "official",
            -(x["confidence"] or 0),
        )):
            if e["id"] in linked_ids:
                continue
            campaign, method = _match(e, campaigns, snapshots_of)
            if campaign is None:
                if e.get("source_role", "primary") == "supporting":
                    stats["supporting_unmatched"] += 1
                    continue
                campaign = _create_campaign(conn, e)
                campaigns.append(campaign)
                method = "seed"
                stats["created"] += 1
            else:
                stats["linked"] += 1
                stats["ai_matches"] += method == "ai"
            snapshots_of.setdefault(campaign["id"], set()).add(e["snapshot_id"])
            conn.execute(
                "INSERT OR IGNORE INTO campaign_sources (campaign_id, extraction_id, match_method) VALUES (?, ?, ?)",
                (campaign["id"], e["id"], method),
            )

        current_by_id = {e["id"]: e for e in current}
        refresh_sql = "SELECT id FROM campaigns"
        refresh_params: tuple = ()
        if bank_id:
            refresh_sql += " WHERE bank_id = ?"
            refresh_params = (bank_id,)
        refresh_ids = [r["id"] for r in conn.execute(refresh_sql, refresh_params)]
        for campaign_id in refresh_ids:
            ext_ids = [r["extraction_id"] for r in conn.execute(
                "SELECT extraction_id FROM campaign_sources WHERE campaign_id = ?", (campaign_id,))]
            supporters = [current_by_id[i] for i in ext_ids if i in current_by_id]
            _refresh_campaign(conn, campaign_id, supporters)
    log.info("validation: %s", stats)
    return stats
