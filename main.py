import argparse
import json
import logging

from config.loader import sync_config
from crawler import runner, source_health
from extractor.extractor import extract_pending
from reporting.enrich import enrich_pending
from reporting.report import as_json, campaign_facts, render_detail, render_summary, render_system, system_summary
from storage.db import init_db, session
from validator.validate import validate


def cmd_init(_):
    init_db()
    print(f"db ready, {sync_config()} sources synced")


def cmd_crawl(a):
    print(runner.crawl(a.bank))


def cmd_extract(a):
    print(extract_pending(a.bank, redo=a.redo))


def cmd_validate(a):
    print(validate(a.bank, rebuild=a.rebuild))


def cmd_discover(a):
    print(f"{runner.discover(a.bank)} new leads (review with: python main.py leads)")


def cmd_validate_leads(a):
    init_db()
    print(runner.validate_leads(a.bank, redo=a.redo, limit=a.limit, lead_id=a.lead))


def cmd_enrich(a):
    init_db()
    print(json.dumps(enrich_pending(
        bank=a.bank, campaign_id=a.campaign, verified_only=a.verified_only,
        redo=a.redo, limit=a.limit
    ), ensure_ascii=False, indent=2))


def cmd_run(a):
    init_db()
    sync_config()
    print("crawl:", runner.crawl(a.bank))
    print("extract:", extract_pending(a.bank))
    print("validate:", validate(a.bank))


def cmd_list(a):
    sql = """SELECT b.code AS bank, c.id, c.product_scope, c.canonical_title, c.campaign_type, c.reward_value,
                    c.start_date, c.end_date, c.verification_status, c.lifecycle_status,
                    c.verification_notes
             FROM campaigns c JOIN banks b ON b.id = c.bank_id WHERE 1=1"""
    params: list = []
    if a.bank:
        sql += " AND b.code = ?"
        params.append(a.bank)
    if a.scope:
        sql += " AND c.product_scope = ?"
        params.append(a.scope)
    if not a.all:
        sql += " AND c.lifecycle_status IN ('upcoming', 'active', 'expiring')"
    sql += " ORDER BY b.code, c.end_date IS NULL, c.end_date"
    with session() as conn:
        for r in conn.execute(sql, params):
            print(json.dumps(dict(r), ensure_ascii=False))


def cmd_sources(_):
    with session() as conn:
        for r in conn.execute(
            """SELECT b.code, s.source_type, s.source_role, s.enabled, s.fetcher, s.url, s.last_error,
                      s.last_attempt_at, s.last_success_at, s.consecutive_failures,
                      s.last_http_status, s.last_latency_ms,
                      (SELECT COUNT(*) FROM raw_snapshots r WHERE r.source_id = s.id) AS snapshots
               FROM sources s JOIN banks b ON b.id = s.bank_id ORDER BY b.code, s.source_type"""):
            print(json.dumps(dict(r), ensure_ascii=False))


def cmd_health(a):
    init_db()
    with session() as conn:
        sql = """SELECT b.code AS bank, s.id, s.source_type, s.source_role, s.enabled, s.fetcher,
                        s.url, s.last_attempt_at, s.last_success_at, s.consecutive_failures,
                        s.last_http_status, s.last_latency_ms, s.last_error
                   FROM sources s JOIN banks b ON b.id = s.bank_id WHERE 1=1"""
        params = []
        if a.bank:
            sql += " AND b.code = ?"
            params.append(a.bank)
        sql += " ORDER BY b.code, s.source_type, s.id"
        rows = [dict(r) for r in conn.execute(sql, params)]
    for row in rows:
        row["health"] = source_health.health_level(
            row["consecutive_failures"], enabled=bool(row["enabled"]),
            last_attempt_at=row["last_attempt_at"]
        )
        print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({"summary": source_health.counts(rows)}, ensure_ascii=False))


def cmd_report(a):
    init_db()
    with session() as conn:
        if a.system:
            result = system_summary(conn)
            print(as_json(result) if a.json else render_system(result))
            return
        facts = campaign_facts(
            conn,
            bank=a.bank,
            campaign_type=a.type,
            verified_only=a.verified_only,
            expiring=a.expiring,
            include_all=a.all,
            campaign_id=a.campaign,
        )
    if a.campaign is not None:
        if not facts:
            raise SystemExit(f"campaign {a.campaign} not found for the selected filters")
        result = facts[0]
        print(as_json(result) if a.json else render_detail(result))
        return
    print(as_json(facts) if a.json else render_summary(facts))


def cmd_leads(a):
    init_db()
    with session() as conn:
        if a.accept or a.reject:
            lead_id, status = (a.accept, "accepted") if a.accept else (a.reject, "rejected")
            if status == "accepted":
                lead = conn.execute("SELECT * FROM discovery_leads WHERE id = ?", (lead_id,)).fetchone()
                if not lead:
                    raise SystemExit(f"lead {lead_id} not found")
                if lead["validation_status"] not in {"recommended", "review"} and not a.force:
                    raise SystemExit(
                        f"lead {lead_id} is validator-{lead['validation_status']}; use --force to accept deliberately"
                    )
                from storage.repo import upsert_source
                fetcher = lead["validation_fetcher"] or "requests"
                role = lead["recommended_role"] or "primary"
                upsert_source(conn, lead["bank_id"], "discovered", lead["found_url"], fetcher, None, role)
                print("accepted; add it to banks.yaml to keep it permanently")
            conn.execute("UPDATE discovery_leads SET status = ? WHERE id = ?", (status, lead_id))
            return
        sql = "SELECT * FROM discovery_leads WHERE 1=1"
        params = []
        if not a.all:
            sql += " AND status = 'pending'"
        if a.recommended:
            sql += " AND validation_status = 'recommended'"
        elif a.review:
            sql += " AND validation_status = 'review'"
        elif a.validator_reject:
            sql += " AND validation_status = 'reject'"
        if a.bank:
            sql += " AND bank_id = (SELECT id FROM banks WHERE code = ?)"
            params.append(a.bank)
        sql += " ORDER BY COALESCE(content_score, -1) DESC, score DESC"
        for r in conn.execute(sql, params):
            print(json.dumps(dict(r), ensure_ascii=False))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="European bank credit card campaign collector")
    sub = p.add_subparsers(required=True)
    for name, fn, help_ in [
        ("init", cmd_init, "create tables and sync config/banks.yaml"),
        ("crawl", cmd_crawl, "fetch sources, snapshot changed pages"),
        ("extract", cmd_extract, "AI-extract campaigns from new snapshots"),
        ("validate", cmd_validate, "cross-validate and update campaigns"),
        ("discover", cmd_discover, "AI-rank links on official pages into leads"),
        ("validate-leads", cmd_validate_leads, "fetch and content-validate discovery leads"),
        ("enrich", cmd_enrich, "GPT-enrich campaign facts and persist evidence-bound action plans"),
        ("run", cmd_run, "init + crawl + extract + validate"),
        ("list", cmd_list, "show campaigns"),
        ("sources", cmd_sources, "show source health"),
        ("health", cmd_health, "show source health levels and failure streaks"),
        ("report", cmd_report, "show user-facing campaign facts and evidence"),
        ("leads", cmd_leads, "review discovery leads"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--bank", help="bank code from config/banks.yaml")
        sp.set_defaults(func=fn)
        if name == "list":
            sp.add_argument("--all", action="store_true", help="include expired/removed")
            sp.add_argument("--scope", choices=["credit_card", "card_bundle"])
        if name == "extract":
            sp.add_argument("--redo", action="store_true", help="discard and redo extractions (after prompt changes)")
        if name == "validate":
            sp.add_argument("--rebuild", action="store_true", help="regenerate campaigns from extractions")
        if name == "validate-leads":
            sp.add_argument("--redo", action="store_true", help="revalidate already-classified pending leads")
            sp.add_argument("--limit", type=int, help="validate at most N leads")
            sp.add_argument("--lead", type=int, help="validate/retry one lead id")
        if name == "enrich":
            sp.add_argument("--campaign", type=int, help="enrich one campaign id")
            sp.add_argument("--verified-only", action="store_true", help="enrich only cross-source verified campaigns")
            sp.add_argument("--redo", action="store_true", help="re-enrich even when current input is cached")
            sp.add_argument("--limit", type=int, help="enrich at most N matching live campaigns")
        if name == "leads":
            sp.add_argument("--accept", type=int)
            sp.add_argument("--reject", type=int)
            sp.add_argument("--recommended", action="store_true", help="show validator-recommended leads")
            sp.add_argument("--review", action="store_true", help="show leads needing manual review")
            sp.add_argument("--validator-reject", action="store_true", help="show validator-rejected leads")
            sp.add_argument("--all", action="store_true", help="include accepted/rejected human-review statuses")
            sp.add_argument("--force", action="store_true", help="allow accepting a validator-rejected/unvalidated lead")
        if name == "report":
            sp.add_argument("--verified-only", action="store_true", help="show only verified campaigns")
            sp.add_argument("--type", help="filter by campaign type, e.g. welcome_bonus")
            sp.add_argument("--expiring", action="store_true", help="show only expiring campaigns")
            sp.add_argument("--all", action="store_true", help="include expired/removed campaigns")
            sp.add_argument("--campaign", type=int, help="show one campaign with full evidence/original-page drill-down")
            sp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
            sp.add_argument("--system", action="store_true", help="show operational system summary instead of campaign facts")
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
