from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

import ai_client
import settings
from storage.db import now_iso, session


log = logging.getLogger(__name__)

FACT_TYPES = (
    "headline",
    "maximum_reward",
    "eligibility",
    "required_step",
    "action_reward",
    "optional_step",
    "deadline",
    "reward_period",
    "exclusion",
    "warning",
)

SYSTEM = """You turn already-extracted bank campaign evidence into a concise action-oriented fact model.
Do not invent facts. Use only the supplied canonical campaign and extraction records.

The purpose is to answer, in plain language:
1. What can the user get?
2. Who is eligible?
3. What must the user do first?
4. Which action/requirement earns which reward?
5. Which steps are optional vs required?
6. What is the deadline/reward period?
7. What exclusions or warnings matter?

Every fact MUST include evidence_refs containing one or more extraction_id values from the supplied evidence.
Only cite extraction IDs that directly support the fact. Keep amounts, thresholds, dates, durations and product
names faithful to the evidence. If a field is not supported, omit it or return null/empty rather than infer it.

Return exactly this JSON shape:
{
  "headline": {"text": string, "evidence_refs": [int]},
  "maximum_reward": {"text": string, "evidence_refs": [int]} | null,
  "eligibility": [{"key": string, "text": string, "evidence_refs": [int]}],
  "required_steps": [{"key": string, "text": string, "evidence_refs": [int]}],
  "action_rewards": [{
      "key": string,
      "action": string,
      "requirement": string | null,
      "reward": string | null,
      "optional": boolean,
      "evidence_refs": [int]
  }],
  "optional_steps": [{"key": string, "text": string, "evidence_refs": [int]}],
  "deadline": {"text": string, "evidence_refs": [int]} | null,
  "reward_period": {"text": string, "evidence_refs": [int]} | null,
  "exclusions": [{"key": string, "text": string, "evidence_refs": [int]}],
  "warnings": [{"key": string, "text": string, "evidence_refs": [int]}]
}
"""

_NUMERIC_TOKEN = re.compile(r"\d+(?:[., ]\d+)*")


def _compact(value: str | None) -> str:
    return " ".join((value or "").split())


def _numeric_tokens(value: Any) -> set[str]:
    if value is None:
        return set()
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    tokens: set[str] = set()
    for match in _NUMERIC_TOKEN.findall(value):
        token = re.sub(r"\s+", "", match)
        parts = re.split(r"[.,]", token)
        if len(parts) > 1 and all(part.isdigit() for part in parts):
            # Treat repeated groups of three digits as thousands separators. This makes
            # 1,060 / 1.060 / 1 060 comparable while preserving ordinary decimals.
            if all(len(part) == 3 for part in parts[1:]):
                token = "".join(parts)
            else:
                token = parts[0] + "." + "".join(parts[1:])
                token = token.rstrip("0").rstrip(".")
        if token:
            tokens.add(token)
    return tokens


def _fact_text(value: dict) -> str:
    return " ".join(
        str(value.get(k) or "")
        for k in ("text", "action", "requirement", "reward")
    ).strip()


def _campaign_input(conn, campaign_id: int) -> tuple[dict, list[dict]]:
    campaign = conn.execute(
        """SELECT c.*, b.code AS bank_code, b.name AS bank_name, b.country,
                  cd.name AS card_name
           FROM campaigns c
           JOIN banks b ON b.id = c.bank_id
           LEFT JOIN cards cd ON cd.id = c.card_id
           WHERE c.id = ?""",
        (campaign_id,),
    ).fetchone()
    if not campaign:
        raise ValueError(f"campaign {campaign_id} not found")
    rows = [dict(r) for r in conn.execute(
        """SELECT e.id AS extraction_id, e.snapshot_id, e.campaign_title, e.offer_summary,
                  e.reward_value, e.conditions, e.start_date, e.end_date, e.language,
                  e.evidence_quote, e.confidence, s.domain, s.source_type, s.source_role,
                  s.url AS source_url, r.fetched_at, r.content_hash
           FROM campaign_sources cs
           JOIN campaign_extractions e ON e.id = cs.extraction_id
           JOIN raw_snapshots r ON r.id = e.snapshot_id
           JOIN sources s ON s.id = r.source_id
           WHERE cs.campaign_id = ?
           ORDER BY e.id""",
        (campaign_id,),
    )]
    return dict(campaign), rows


def _input_hash(campaign: dict, extractions: list[dict]) -> str:
    payload = {
        "campaign": {
            k: campaign.get(k)
            for k in (
                "id", "bank_code", "bank_name", "country", "card_name", "product_scope",
                "canonical_title", "campaign_type", "offer_summary", "reward_value", "conditions",
                "start_date", "end_date", "verification_status", "lifecycle_status",
            )
        },
        "extractions": extractions,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt(campaign: dict, extractions: list[dict]) -> str:
    canonical = {
        "campaign_id": campaign["id"],
        "bank": campaign["bank_name"],
        "country": campaign["country"],
        "product": campaign.get("card_name") or campaign.get("product_scope"),
        "title": campaign["canonical_title"],
        "type": campaign["campaign_type"],
        "summary": campaign.get("offer_summary"),
        "reward": campaign.get("reward_value"),
        "conditions": campaign.get("conditions"),
        "start_date": campaign.get("start_date"),
        "end_date": campaign.get("end_date"),
    }
    evidence = []
    for row in extractions:
        evidence.append({
            "extraction_id": row["extraction_id"],
            "domain": row["domain"],
            "source_type": row["source_type"],
            "title": row["campaign_title"],
            "summary": row["offer_summary"],
            "reward": row["reward_value"],
            "conditions": row["conditions"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "evidence_quote": row["evidence_quote"],
            "confidence": row["confidence"],
        })
    return (
        "Canonical campaign:\n" + json.dumps(canonical, ensure_ascii=False, indent=2)
        + "\n\nSupporting extraction evidence:\n" + json.dumps(evidence, ensure_ascii=False, indent=2)
    )


def _items(result: dict) -> list[tuple[str, dict]]:
    mapping = [
        ("headline", "headline", False),
        ("maximum_reward", "maximum_reward", False),
        ("eligibility", "eligibility", True),
        ("required_step", "required_steps", True),
        ("action_reward", "action_rewards", True),
        ("optional_step", "optional_steps", True),
        ("deadline", "deadline", False),
        ("reward_period", "reward_period", False),
        ("exclusion", "exclusions", True),
        ("warning", "warnings", True),
    ]
    out: list[tuple[str, dict]] = []
    for fact_type, key, many in mapping:
        value = result.get(key)
        if value is None:
            continue
        values = value if many else [value]
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        for item in values:
            if not isinstance(item, dict):
                raise ValueError(f"{key} item must be an object")
            out.append((fact_type, item))
    return out


def _validate_result(result: Any, extractions: list[dict]) -> list[tuple[str, dict, list[int], float]]:
    if not isinstance(result, dict):
        raise ValueError("enrichment result must be an object")
    allowed = {int(e["extraction_id"]): e for e in extractions}
    if not allowed:
        raise ValueError("campaign has no supporting extractions")

    validated: list[tuple[str, dict, list[int], float]] = []
    for fact_type, item in _items(result):
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"{fact_type} has no evidence_refs")
        try:
            refs = list(dict.fromkeys(int(ref) for ref in refs))
        except (TypeError, ValueError):
            raise ValueError(f"{fact_type} has invalid evidence_refs") from None
        unknown = [ref for ref in refs if ref not in allowed]
        if unknown:
            raise ValueError(f"{fact_type} cites extraction(s) outside campaign: {unknown}")

        clean = {k: v for k, v in item.items() if k != "evidence_refs"}
        if not _fact_text(clean):
            raise ValueError(f"{fact_type} is empty")

        evidence_number_sets = [
            _numeric_tokens(_compact(str(allowed[ref].get(k) or "")))
            for ref in refs
            for k in ("campaign_title", "offer_summary", "reward_value", "conditions", "start_date", "end_date", "evidence_quote")
        ]
        evidence_numbers = set().union(*evidence_number_sets) if evidence_number_sets else set()
        unsupported_numbers = _numeric_tokens(_fact_text(clean)) - evidence_numbers
        if unsupported_numbers:
            raise ValueError(f"{fact_type} contains unsupported numeric tokens: {sorted(unsupported_numbers)}")

        confidence = min(float(allowed[ref].get("confidence") or 0) for ref in refs)
        validated.append((fact_type, clean, refs, confidence))

    if not any(t == "headline" for t, *_ in validated):
        raise ValueError("headline is required")
    return validated


def _persist(conn, campaign_id: int, input_hash: str, raw: dict,
             facts: list[tuple[str, dict, list[int], float]], extractions: list[dict]) -> None:
    ts = now_iso()
    snapshot_of = {int(e["extraction_id"]): int(e["snapshot_id"]) for e in extractions}
    conn.execute("DELETE FROM campaign_facts WHERE campaign_id = ?", (campaign_id,))
    positions: dict[str, int] = {}
    for fact_type, value, refs, confidence in facts:
        position = positions.get(fact_type, 0)
        positions[fact_type] = position + 1
        key = value.get("key")
        cur = conn.execute(
            """INSERT INTO campaign_facts(campaign_id,fact_type,fact_key,position,value_json,confidence,generated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (campaign_id, fact_type, key, position,
             json.dumps(value, ensure_ascii=False, sort_keys=True), confidence, ts),
        )
        fact_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO fact_evidence(fact_id,extraction_id,snapshot_id) VALUES (?,?,?)",
            [(fact_id, ref, snapshot_of[ref]) for ref in refs],
        )
    conn.execute(
        """INSERT INTO campaign_enrichments(campaign_id,input_hash,model,status,generated_at,raw_json,error)
           VALUES (?,?,?,?,?,?,NULL)
           ON CONFLICT(campaign_id) DO UPDATE SET input_hash=excluded.input_hash, model=excluded.model,
             status='complete', generated_at=excluded.generated_at, raw_json=excluded.raw_json, error=NULL""",
        (campaign_id, input_hash, settings.AI_MODEL, "complete", ts,
         json.dumps(raw, ensure_ascii=False, sort_keys=True)),
    )


def enrich_campaign(campaign_id: int, *, redo: bool = False) -> dict:
    with session() as conn:
        campaign, extractions = _campaign_input(conn, campaign_id)
        fingerprint = _input_hash(campaign, extractions)
        existing = conn.execute(
            "SELECT * FROM campaign_enrichments WHERE campaign_id = ?", (campaign_id,)
        ).fetchone()
        if existing and existing["status"] == "complete" and existing["input_hash"] == fingerprint and not redo:
            return {"campaign_id": campaign_id, "status": "cached", "facts": conn.execute(
                "SELECT COUNT(*) FROM campaign_facts WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()[0]}

    try:
        result = ai_client.chat_json(SYSTEM, _prompt(campaign, extractions))
        facts = _validate_result(result, extractions)
        with session() as conn:
            _persist(conn, campaign_id, fingerprint, result, facts, extractions)
        return {"campaign_id": campaign_id, "status": "enriched", "facts": len(facts)}
    except Exception as exc:
        log.exception("enrichment failed for campaign %s", campaign_id)
        with session() as conn:
            conn.execute(
                """INSERT INTO campaign_enrichments(campaign_id,input_hash,model,status,generated_at,raw_json,error)
                   VALUES (?,?,?,?,?,NULL,?)
                   ON CONFLICT(campaign_id) DO UPDATE SET input_hash=excluded.input_hash, model=excluded.model,
                     status='error', generated_at=excluded.generated_at, raw_json=NULL, error=excluded.error""",
                (campaign_id, fingerprint, settings.AI_MODEL, "error", now_iso(), str(exc)),
            )
        return {"campaign_id": campaign_id, "status": "error", "error": str(exc)}


def enrich_pending(*, bank: str | None = None, campaign_id: int | None = None,
                   verified_only: bool = False, redo: bool = False, limit: int | None = None) -> dict:
    with session() as conn:
        sql = """SELECT c.id FROM campaigns c JOIN banks b ON b.id=c.bank_id
                 WHERE c.lifecycle_status IN ('upcoming','active','expiring')"""
        params: list[Any] = []
        if bank:
            sql += " AND b.code = ?"
            params.append(bank)
        if campaign_id is not None:
            sql += " AND c.id = ?"
            params.append(campaign_id)
        if verified_only:
            sql += " AND c.verification_status = 'verified'"
        sql += " ORDER BY c.id"
        ids = [r[0] for r in conn.execute(sql, params)]
    if limit is not None:
        ids = ids[:limit]
    stats = {"enriched": 0, "cached": 0, "error": 0, "results": []}
    for cid in ids:
        result = enrich_campaign(cid, redo=redo)
        stats["results"].append(result)
        stats[result["status"]] = stats.get(result["status"], 0) + 1
    return stats


def load_enrichment(conn, campaign_id: int) -> dict:
    """Load persisted facts only when they still match current campaign/extraction input."""
    row = conn.execute(
        "SELECT * FROM campaign_enrichments WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if not row:
        return {"status": "missing", "facts": {}}
    row = dict(row)
    if row["status"] != "complete":
        return {"status": row["status"], "error": row.get("error"), "facts": {}}
    campaign, extractions = _campaign_input(conn, campaign_id)
    if _input_hash(campaign, extractions) != row["input_hash"]:
        return {"status": "stale", "generated_at": row["generated_at"], "facts": {}}

    fact_rows = [dict(r) for r in conn.execute(
        """SELECT id, fact_type, fact_key, position, value_json, confidence, generated_at
           FROM campaign_facts WHERE campaign_id = ? ORDER BY fact_type, position, id""",
        (campaign_id,),
    )]
    evidence_by_fact: dict[int, list[dict]] = {}
    for e in conn.execute(
        """SELECT fe.fact_id, fe.extraction_id, fe.snapshot_id, s.domain, s.url AS source_url,
                  ce.evidence_quote, ce.confidence AS extraction_confidence
           FROM fact_evidence fe
           JOIN campaign_extractions ce ON ce.id = fe.extraction_id
           JOIN raw_snapshots rs ON rs.id = fe.snapshot_id
           JOIN sources s ON s.id = rs.source_id
           JOIN campaign_facts cf ON cf.id = fe.fact_id
           WHERE cf.campaign_id = ?
           ORDER BY fe.fact_id, s.domain, fe.extraction_id""",
        (campaign_id,),
    ):
        evidence_by_fact.setdefault(e["fact_id"], []).append(dict(e))

    grouped: dict[str, list[dict]] = {fact_type: [] for fact_type in FACT_TYPES}
    for fact in fact_rows:
        value = json.loads(fact["value_json"])
        value["fact_id"] = fact["id"]
        value["confidence"] = fact["confidence"]
        value["evidence_refs"] = evidence_by_fact.get(fact["id"], [])
        grouped[fact["fact_type"]].append(value)
    return {
        "status": "complete",
        "model": row["model"],
        "generated_at": row["generated_at"],
        "facts": grouped,
    }

