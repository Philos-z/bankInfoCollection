import argparse
import json
import logging

from config.loader import sync_config
from crawler import runner, source_health
from extractor.extractor import extract_pending
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
        ("run", cmd_run, "init + crawl + extract + validate"),
        ("list", cmd_list, "show campaigns"),
        ("sources", cmd_sources, "show source health"),
        ("health", cmd_health, "show source health levels and failure streaks"),
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
        if name == "leads":
            sp.add_argument("--accept", type=int)
            sp.add_argument("--reject", type=int)
            sp.add_argument("--recommended", action="store_true", help="show validator-recommended leads")
            sp.add_argument("--review", action="store_true", help="show leads needing manual review")
            sp.add_argument("--validator-reject", action="store_true", help="show validator-rejected leads")
            sp.add_argument("--all", action="store_true", help="include accepted/rejected human-review statuses")
            sp.add_argument("--force", action="store_true", help="allow accepting a validator-rejected/unvalidated lead")
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
