from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

from reporting.enrich import load_enrichment
from validator.rules import MIN_CONFIDENCE_FOR_VERIFY


LIVE_LIFECYCLES = ("upcoming", "active", "expiring")
SCOPE_LABELS = {
    "credit_card": "Credit card",
    "card_bundle": "Account / card bundle",
}


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).strip().lower()


def _distinct(values: Iterable[str | None], *, exclude: str | None = None) -> list[str]:
    excluded = _norm(exclude)
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value:
            continue
        key = _norm(value)
        if not key or key == excluded or key in seen:
            continue
        seen.add(key)
        out.append(value.strip())
    return out


def _campaign_rows(
    conn,
    *,
    bank: str | None = None,
    campaign_type: str | None = None,
    verified_only: bool = False,
    expiring: bool = False,
    include_all: bool = False,
    campaign_id: int | None = None,
) -> list[dict]:
    sql = """
        SELECT c.id, c.bank_id, b.code AS bank_code, b.name AS bank_name, b.country,
               b.website AS bank_website, c.card_id, cd.name AS card_name,
               c.product_scope, c.canonical_title, c.campaign_type, c.offer_summary,
               c.reward_value, c.conditions, c.start_date, c.end_date,
               c.verification_status, c.verification_notes, c.lifecycle_status,
               c.primary_extraction_id, c.first_seen_at, c.last_seen_at, c.last_verified_at
        FROM campaigns c
        JOIN banks b ON b.id = c.bank_id
        LEFT JOIN cards cd ON cd.id = c.card_id
        WHERE 1=1
    """
    params: list = []
    if campaign_id is not None:
        sql += " AND c.id = ?"
        params.append(campaign_id)
    if bank:
        sql += " AND b.code = ?"
        params.append(bank)
    if campaign_type:
        sql += " AND c.campaign_type = ?"
        params.append(campaign_type)
    if verified_only:
        sql += " AND c.verification_status = 'verified'"
    if expiring:
        sql += " AND c.lifecycle_status = 'expiring'"
    elif not include_all:
        placeholders = ",".join("?" for _ in LIVE_LIFECYCLES)
        sql += f" AND c.lifecycle_status IN ({placeholders})"
        params.extend(LIVE_LIFECYCLES)
    sql += " ORDER BY b.code, c.end_date IS NULL, c.end_date, c.id"
    return [dict(r) for r in conn.execute(sql, params)]


def _supporters(conn, campaign_ids: list[int]) -> dict[int, list[dict]]:
    if not campaign_ids:
        return {}
    placeholders = ",".join("?" for _ in campaign_ids)
    sql = f"""
        SELECT cs.campaign_id, cs.match_method,
               e.id AS extraction_id, e.campaign_title, e.offer_summary, e.reward_value,
               e.conditions, e.start_date, e.end_date, e.language, e.evidence_quote,
               e.confidence, e.extraction_method, e.extracted_at,
               s.id AS source_id, s.domain, s.source_type, s.source_role, s.url AS source_url,
               r.id AS snapshot_id, r.url AS snapshot_url, r.fetched_at, r.http_status,
               r.content_hash, r.raw_content_path, r.text_content_path, r.selector_used
        FROM campaign_sources cs
        JOIN campaign_extractions e ON e.id = cs.extraction_id
        JOIN raw_snapshots r ON r.id = e.snapshot_id
        JOIN sources s ON s.id = r.source_id
        WHERE cs.campaign_id IN ({placeholders})
        ORDER BY cs.campaign_id, s.id, r.fetched_at DESC, e.id DESC
    """
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in conn.execute(sql, campaign_ids):
        grouped[row["campaign_id"]].append(dict(row))
    return grouped


def _aggregate_provenance(rows: list[dict]) -> tuple[list[dict], list[str], int]:
    by_source: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        by_source[row["source_id"]].append(row)

    sources: list[dict] = []
    verification_domains = {
        row["domain"]
        for row in rows
        if (row.get("confidence") or 0) >= MIN_CONFIDENCE_FOR_VERIFY
    }
    for source_rows in by_source.values():
        # _supporters() already orders newest first for a source.
        latest = source_rows[0]
        counts = any((row.get("confidence") or 0) >= MIN_CONFIDENCE_FOR_VERIFY for row in source_rows)

        def evidence_version(row: dict) -> dict:
            return {
                "extraction_id": row["extraction_id"],
                "snapshot_id": row["snapshot_id"],
                "snapshot_url": row["snapshot_url"],
                "fetched_at": row["fetched_at"],
                "http_status": row["http_status"],
                "content_hash": row["content_hash"],
                "selector_used": row["selector_used"],
                "raw_snapshot_path": row["raw_content_path"],
                "text_snapshot_path": row["text_content_path"],
                "campaign_title": row["campaign_title"],
                "offer_summary": row["offer_summary"],
                "reward_value": row["reward_value"],
                "conditions": row["conditions"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "language": row["language"],
                "evidence_quote": row["evidence_quote"],
                "confidence": row["confidence"],
                "match_method": row["match_method"],
                "extraction_method": row["extraction_method"],
                "extracted_at": row["extracted_at"],
            }

        history = [evidence_version(row) for row in source_rows]
        sources.append({
            "source_id": latest["source_id"],
            "domain": latest["domain"],
            "source_type": latest["source_type"],
            "source_role": latest["source_role"],
            "live_url": latest["source_url"],
            "counts_for_verification": counts,
            "version_count": len(source_rows),
            "latest_evidence": history[0],
            "evidence_history": history,
        })
    sources.sort(key=lambda x: (x["source_type"] != "official", x["domain"], x["source_id"]))
    return sources, sorted(verification_domains), len(verification_domains)


def _build_fact(campaign: dict, supporter_rows: list[dict]) -> dict:
    sources, verification_domains, independent_source_count = _aggregate_provenance(supporter_rows)
    latest_claims = [s["latest_evidence"] for s in sources]
    supporting_conditions = _distinct(
        (c.get("conditions") for c in latest_claims), exclude=campaign.get("conditions")
    )
    supporting_summaries = _distinct(
        (c.get("offer_summary") for c in latest_claims), exclude=campaign.get("offer_summary")
    )

    return {
        "campaign_id": campaign["id"],
        "bank": {
            "code": campaign["bank_code"],
            "name": campaign["bank_name"],
            "country": campaign["country"],
            "website": campaign["bank_website"],
        },
        "product": {
            "name": campaign["card_name"],
            "scope": campaign["product_scope"],
            "scope_label": SCOPE_LABELS.get(campaign["product_scope"], campaign["product_scope"]),
        },
        "campaign": {
            "title": campaign["canonical_title"],
            "type": campaign["campaign_type"],
            "summary": campaign["offer_summary"],
            "reward": campaign["reward_value"],
            "conditions": campaign["conditions"],
            "supporting_summaries": supporting_summaries,
            "supporting_conditions": supporting_conditions,
        },
        "validity": {
            "start_date": campaign["start_date"],
            "end_date": campaign["end_date"],
            "lifecycle_status": campaign["lifecycle_status"],
            "first_seen_at": campaign["first_seen_at"],
            "last_seen_at": campaign["last_seen_at"],
        },
        "verification": {
            "status": campaign["verification_status"],
            "notes": campaign["verification_notes"],
            "independent_source_count": independent_source_count,
            "independent_domains": verification_domains,
            "last_verified_at": campaign["last_verified_at"],
        },
        "provenance": {
            "primary_extraction_id": campaign["primary_extraction_id"],
            "sources": sources,
        },
    }


def campaign_facts(
    conn,
    *,
    bank: str | None = None,
    campaign_type: str | None = None,
    verified_only: bool = False,
    expiring: bool = False,
    include_all: bool = False,
    campaign_id: int | None = None,
) -> list[dict]:
    campaigns = _campaign_rows(
        conn,
        bank=bank,
        campaign_type=campaign_type,
        verified_only=verified_only,
        expiring=expiring,
        include_all=include_all,
        campaign_id=campaign_id,
    )
    supporters = _supporters(conn, [c["id"] for c in campaigns])
    results = []
    for campaign in campaigns:
        fact = _build_fact(campaign, supporters.get(campaign["id"], []))
        fact["action_plan"] = load_enrichment(conn, campaign["id"])
        results.append(fact)
    return results


def system_summary(conn) -> dict:
    source = dict(conn.execute(
        """SELECT COUNT(*) total,
                  SUM(enabled = 1) enabled,
                  SUM(enabled = 0) disabled,
                  SUM(enabled = 1 AND consecutive_failures > 0) enabled_with_failures
           FROM sources"""
    ).fetchone())
    campaign = dict(conn.execute(
        """SELECT COUNT(*) total,
                  SUM(lifecycle_status IN ('upcoming','active','expiring')) live,
                  SUM(verification_status = 'verified') verified,
                  SUM(verification_status = 'conflict') conflicts,
                  SUM(lifecycle_status = 'expiring') expiring
           FROM campaigns"""
    ).fetchone())
    pending_snapshots = conn.execute(
        "SELECT COUNT(*) FROM raw_snapshots WHERE extracted = 0"
    ).fetchone()[0]
    unvalidated_leads = conn.execute(
        "SELECT COUNT(*) FROM discovery_leads WHERE status = 'pending' AND validation_status = 'unvalidated'"
    ).fetchone()[0]
    return {
        "sources": source,
        "campaigns": campaign,
        "pending_snapshots": pending_snapshots,
        "pending_unvalidated_leads": unvalidated_leads,
    }


def _line(label: str, value) -> str:
    return f"{label}: {value if value not in (None, '') else '-'}"


def _fact_domains(item: dict) -> str:
    domains = sorted({ref.get("domain") for ref in item.get("evidence_refs", []) if ref.get("domain")})
    return ", ".join(domains)


def render_summary(facts: list[dict]) -> str:
    verified = sum(1 for f in facts if f["verification"]["status"] == "verified")
    lines = [f"Campaign Report — {len(facts)} campaign(s), {verified} verified", ""]
    for idx, fact in enumerate(facts, 1):
        bank = fact["bank"]["name"]
        product = fact["product"]["name"] or fact["product"]["scope_label"]
        campaign = fact["campaign"]
        validity = fact["validity"]
        verification = fact["verification"]
        action_plan = fact.get("action_plan") or {}
        enriched = action_plan.get("status") == "complete"
        headline = None
        max_reward = None
        actions: list[dict] = []
        if enriched:
            grouped = action_plan["facts"]
            headline = (grouped.get("headline") or [{}])[0].get("text")
            max_reward = (grouped.get("maximum_reward") or [{}])[0].get("text")
            actions = grouped.get("action_reward") or []
        lines.extend([
            f"{idx}. [{verification['status'].upper()}] {bank} — {product}",
            f"   {campaign['title']}",
            f"   What you can get: {max_reward or campaign['reward'] or '-'}",
            f"   {headline or campaign['summary'] or '-'}",
            *([f"   Action: {a.get('action') or '-'} → {a.get('reward') or '-'}" for a in actions[:3]] if actions else
              [f"   Conditions: {campaign['conditions'] or '-'}"]),
            f"   Validity: {validity['start_date'] or '-'} → {validity['end_date'] or '-'} "
            f"({validity['lifecycle_status']})",
            f"   Sources: {verification['independent_source_count']} independent domain(s) | "
            f"Campaign ID: {fact['campaign_id']} | Facts: {action_plan.get('status', 'missing')}",
            "",
        ])
    return "\n".join(lines).rstrip()


def render_detail(fact: dict) -> str:
    c = fact["campaign"]
    v = fact["validity"]
    ver = fact["verification"]
    lines = [
        f"Campaign #{fact['campaign_id']}",
        "=" * 72,
        _line("Bank", f"{fact['bank']['name']} ({fact['bank']['code']})"),
        _line("Product", fact["product"]["name"] or fact["product"]["scope_label"]),
        _line("Title", c["title"]),
        _line("Type", c["type"]),
        _line("Reward", c["reward"]),
        _line("Summary", c["summary"]),
        _line("Conditions", c["conditions"]),
        _line("Validity", f"{v['start_date'] or '-'} → {v['end_date'] or '-'} ({v['lifecycle_status']})"),
        _line("Verification", f"{ver['status']} · {ver['independent_source_count']} independent domain(s)"),
        _line("Domains", ", ".join(ver["independent_domains"]) or "-"),
    ]
    action_plan = fact.get("action_plan") or {"status": "missing", "facts": {}}
    lines.extend(["", "Action plan"])
    if action_plan.get("status") == "complete":
        grouped = action_plan["facts"]
        headline = (grouped.get("headline") or [{}])[0].get("text")
        maximum = (grouped.get("maximum_reward") or [{}])[0].get("text")
        if headline:
            lines.append(f"  {headline}")
        if maximum:
            lines.append(f"  Maximum reward: {maximum}")
        if grouped.get("eligibility"):
            lines.append("\n  Eligibility:")
            lines.extend(
                f"    - {item.get('text')} [evidence: {_fact_domains(item) or '-'}]"
                for item in grouped["eligibility"]
            )
        if grouped.get("required_step"):
            lines.append("\n  Required first steps:")
            lines.extend(
                f"    - {item.get('text')} [evidence: {_fact_domains(item) or '-'}]"
                for item in grouped["required_step"]
            )
        if grouped.get("action_reward"):
            lines.append("\n  What to do → what you get:")
            for item in grouped["action_reward"]:
                requirement = f" ({item.get('requirement')})" if item.get("requirement") else ""
                optional = "optional" if item.get("optional") else "required"
                lines.append(
                    f"    - {item.get('action')}{requirement} → {item.get('reward') or '-'} "
                    f"[{optional}; evidence: {_fact_domains(item) or '-'}]"
                )
        if grouped.get("optional_step"):
            lines.append("\n  Optional steps:")
            lines.extend(f"    - {item.get('text')}" for item in grouped["optional_step"])
        deadline = (grouped.get("deadline") or [{}])[0].get("text")
        reward_period = (grouped.get("reward_period") or [{}])[0].get("text")
        if deadline:
            lines.append(f"\n  Deadline: {deadline}")
        if reward_period:
            lines.append(f"  Reward period: {reward_period}")
        if grouped.get("exclusion"):
            lines.append("\n  Exclusions:")
            lines.extend(f"    - {item.get('text')}" for item in grouped["exclusion"])
        if grouped.get("warning"):
            lines.append("\n  Warnings:")
            lines.extend(f"    - {item.get('text')}" for item in grouped["warning"])
        lines.append(
            f"\n  Facts generated: {action_plan.get('generated_at')} with {action_plan.get('model')}"
        )
    else:
        lines.append(
            f"  Structured facts: {action_plan.get('status', 'missing')} "
            f"(run: python main.py enrich --campaign {fact['campaign_id']})"
        )
    if c["supporting_conditions"]:
        lines.extend(["", "Additional conditions found in supporting sources:"])
        lines.extend(f"  - {value}" for value in c["supporting_conditions"])
    lines.extend(["", "Evidence / original pages"])
    for idx, source in enumerate(fact["provenance"]["sources"], 1):
        e = source["latest_evidence"]
        lines.extend([
            f"\n[{idx}] {source['domain']} · {source['source_type']} · {source['source_role']}",
            _line("Live URL", source["live_url"]),
            _line("Fetched", e["fetched_at"]),
            _line("Snapshot ID", e["snapshot_id"]),
            _line("Raw snapshot", e["raw_snapshot_path"]),
            _line("Text snapshot", e["text_snapshot_path"]),
            _line("Confidence", e["confidence"]),
            _line("Evidence", e["evidence_quote"]),
            _line("Source conditions", e["conditions"]),
            _line("Versions retained", source["version_count"]),
        ])
    return "\n".join(lines)


def render_system(summary: dict) -> str:
    s, c = summary["sources"], summary["campaigns"]
    return "\n".join([
        "System Report",
        "=============",
        f"Sources: {s['enabled']} enabled / {s['disabled']} disabled / {s['enabled_with_failures']} enabled with failures",
        f"Campaigns: {c['live']} live / {c['verified']} verified / {c['conflicts']} conflicts / {c['expiring']} expiring",
        f"Pending snapshots: {summary['pending_snapshots']}",
        f"Pending unvalidated leads: {summary['pending_unvalidated_leads']}",
    ])


def as_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

