import logging
import re
from datetime import date

import ai_client
import settings
from extractor.schema import OUTPUT_SPEC, clean_campaign
from storage import repo
from storage.db import now_iso, session

log = logging.getLogger(__name__)

CHUNK_CHARS = 24000

SYSTEM = f"""You extract card promotions from European bank web pages (any language).
Extract time-bound or conditional promotional offers (welcome bonus, cashback, points boosts, fee waivers,
0% periods, referral rewards, merchant promos) in two scopes:
- product_scope "credit_card": the offer is tied to a credit or charge card.
- product_scope "card_bundle": account-opening, bank-switch or package offers that include a payment card
  (for example a new-customer account bonus bundled with a payment card).
Ignore generic product features that are not promotions, and offers on loans, mortgages, savings or
insurance-only products.
Resolve dates to ISO YYYY-MM-DD (e.g. "bis 31.12.2026" -> end_date 2026-12-31; "valid until 30 June"
-> use the year implied by context). Leave dates null if the page does not state them - never guess.
The evidence_quote must be copied from the supplied Page text. Prefer one exact contiguous quote. If you must omit
a middle clause, use an explicit [...] only between otherwise verbatim source fragments; never paraphrase evidence.
For card_name, use the exact card product name from the offer block, its nearest heading, or clearly associated
page context. Do not leave card_name null merely because the product name is omitted from the offer sentence
itself. However, if more than one card could plausibly own the offer, leave card_name null rather than guessing.
For product_scope "card_bundle", account/package tier names are NOT card names. If one account-opening/bank-switch
offer applies to several package variants, output ONE
campaign record for the shared offer and leave card_name null unless a specific payment-card product is explicitly
the subject of the promotion.
Deduplicate repeated page content: if the same card promotion is described more than once on the same page
(for example once in prose and again in a comparison table), output it only once.
If one offer applies to multiple separately named card products or variants, output one campaign record per
distinct card product. Never combine distinct card names into one card_name with '/', 'or', 'bzw.', '&', etc.
Separately named colour/edition/tier variants must remain separate even when their bonus mechanics are identical.
If there are no promotions, return {{"campaigns": []}}.
Output schema:
{OUTPUT_SPEC}"""


def _norm(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip().lower()


def _dedupe_bundle_variants(campaigns: list[dict]) -> list[dict]:
    """Collapse one shared card-bundle offer accidentally emitted once per package tier.

    This is intentionally limited to card_bundle and requires the same grounded evidence quote,
    title, reward and dates. Credit-card colour/edition/tier variants are untouched.
    """
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for c in campaigns:
        if c.get("product_scope") != "card_bundle":
            key = ("unique", id(c))
        else:
            key = (
                "bundle",
                c.get("campaign_type"),
                _norm(c.get("campaign_title")),
                _norm(c.get("reward_value")),
                c.get("start_date"),
                c.get("end_date"),
                _norm(c.get("evidence_quote")),
            )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    result: list[dict] = []
    for key in order:
        items = groups[key]
        if key[0] == "bundle" and len(items) > 1:
            merged = dict(max(items, key=lambda x: x.get("confidence") or 0))
            # The shared evidence proves an umbrella bundle campaign, not one specific package/card.
            merged["card_name"] = None
            result.append(merged)
        else:
            result.extend(items)
    return result


def _chunks(text: str) -> list[str]:
    if len(text) <= CHUNK_CHARS:
        return [text]
    paras, chunks, buf = text.split("\n"), [], ""
    for p in paras:
        if len(buf) + len(p) > CHUNK_CHARS and buf:
            chunks.append(buf)
            buf = ""
        buf += p + "\n"
    if buf:
        chunks.append(buf)
    return chunks


def extract_snapshot(snapshot) -> int:
    text = (settings.ROOT / snapshot["text_content_path"]).read_text(encoding="utf-8")
    header = (
        f"Today: {date.today().isoformat()}\nBank: {snapshot['bank_name']} ({snapshot['country']})\n"
        f"Source URL: {snapshot['url']}\nSource type: {snapshot['source_type']}\n\n"
    )
    campaigns: list[dict] = []
    for i, chunk in enumerate(_chunks(text)):
        result = ai_client.chat_json(SYSTEM, f"{header}Page text (part {i + 1}):\n{chunk}")
        items = result.get("campaigns", []) if isinstance(result, dict) else []
        campaigns += [c for c in (clean_campaign(x, text) for x in items if isinstance(x, dict)) if c]
    campaigns = _dedupe_bundle_variants(campaigns)

    with session() as conn:
        for c in campaigns:
            card_id = repo.get_or_create_card(conn, snapshot["bank_id"], c["card_name"])
            conn.execute(
                """INSERT INTO campaign_extractions
                   (snapshot_id, bank_id, card_id, product_scope, campaign_title, campaign_type, offer_summary,
                    reward_value, conditions, start_date, end_date, language, evidence_quote,
                    confidence, extraction_method, extracted_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ai', ?)""",
                (snapshot["id"], snapshot["bank_id"], card_id, c["product_scope"], c["campaign_title"], c["campaign_type"],
                 c["offer_summary"], c["reward_value"], c["conditions"], c["start_date"], c["end_date"],
                 c["language"], c["evidence_quote"], c["confidence"], now_iso()),
            )
        conn.execute("UPDATE raw_snapshots SET extracted = 1 WHERE id = ?", (snapshot["id"],))
    return len(campaigns)


def reset_extractions(conn, bank_code: str | None) -> int:
    """Drop AI extractions so snapshots get re-extracted (e.g. after a prompt change)."""
    scope = "SELECT r.id FROM raw_snapshots r JOIN sources s ON s.id = r.source_id JOIN banks b ON b.id = s.bank_id"
    params: tuple = ()
    if bank_code:
        scope += " WHERE b.code = ?"
        params = (bank_code,)
    extraction_ids = f"SELECT id FROM campaign_extractions WHERE snapshot_id IN ({scope})"
    conn.execute(f"UPDATE campaigns SET primary_extraction_id = NULL WHERE primary_extraction_id IN ({extraction_ids})", params)
    conn.execute(f"DELETE FROM campaign_sources WHERE extraction_id IN ({extraction_ids})", params)
    conn.execute(f"DELETE FROM campaign_extractions WHERE snapshot_id IN ({scope})", params)
    return conn.execute(f"UPDATE raw_snapshots SET extracted = 0 WHERE id IN ({scope})", params).rowcount


def extract_pending(bank_code: str | None = None, redo: bool = False) -> dict:
    if redo:
        with session() as conn:
            log.info("reset %d snapshots for re-extraction", reset_extractions(conn, bank_code))
    with session() as conn:
        sql = """SELECT r.*, s.bank_id, s.source_type, b.name AS bank_name, b.country
                 FROM raw_snapshots r JOIN sources s ON s.id = r.source_id
                 JOIN banks b ON b.id = s.bank_id WHERE r.extracted = 0"""
        params: tuple = ()
        if bank_code:
            sql += " AND b.code = ?"
            params = (bank_code,)
        snapshots = conn.execute(sql, params).fetchall()

    stats = {"snapshots": 0, "campaigns": 0, "errors": 0}
    for snap in snapshots:
        try:
            stats["campaigns"] += extract_snapshot(snap)
            stats["snapshots"] += 1
        except Exception as e:
            stats["errors"] += 1
            log.warning("extraction failed for snapshot %s (%s): %s", snap["id"], snap["url"], e)
    return stats
