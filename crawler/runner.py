import hashlib
import logging
import time
from urllib.parse import urlparse

import settings
from crawler import ai_discovery, ai_fallback, content_validator, source_health
from crawler.fetch import fetch, html_to_text, pdf_to_text
from storage import repo
from storage.db import session

log = logging.getLogger(__name__)

MIN_TEXT_CHARS = 200


class SourceCrawlError(RuntimeError):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


def _save_snapshot_files(source_id: int, content_hash: str, raw: bytes, text: str, is_pdf: bool) -> tuple[str, str]:
    folder = settings.SNAPSHOT_DIR / str(source_id)
    folder.mkdir(parents=True, exist_ok=True)
    raw_path = folder / f"{content_hash}.{'pdf' if is_pdf else 'html'}"
    text_path = folder / f"{content_hash}.txt"
    raw_path.write_bytes(raw)
    text_path.write_text(text, encoding="utf-8")
    return str(raw_path.relative_to(settings.ROOT)), str(text_path.relative_to(settings.ROOT))


def crawl_source(source) -> tuple[str, int | None]:
    """Fetch one source, self-heal its selector if needed, snapshot if content changed."""
    result = fetch(source["url"], source["fetcher"])
    if result.status and result.status >= 400:
        raise SourceCrawlError(f"HTTP {result.status}", result.status)

    is_pdf = "pdf" in result.content_type or source["url"].lower().endswith(".pdf")
    selector = source["selector"]
    healed = False
    if is_pdf:
        text = pdf_to_text(result.body)
    else:
        text = html_to_text(result.html or result.body.decode("utf-8", "replace"), selector)
        if len(text) < MIN_TEXT_CHARS and selector:
            new_selector = ai_fallback.heal_selector(result.html or "", source["url"], selector)
            if new_selector:
                selector, healed = new_selector, True
                text = html_to_text(result.html, selector)
                log.info("selector healed for %s: %s", source["url"], selector)
        if len(text) < MIN_TEXT_CHARS:
            # Fall back to whole-page text rather than losing the snapshot.
            text = html_to_text(result.html or "", None)
            selector = None

    if len(text) < MIN_TEXT_CHARS:
        raise SourceCrawlError("page produced no usable text (blocked or JS-only?)", result.status)

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    with session() as conn:
        if healed:
            conn.execute("UPDATE sources SET selector = ? WHERE id = ?", (selector, source["id"]))
        if repo.latest_snapshot_hash(conn, source["id"]) == content_hash:
            return "unchanged", result.status
        raw_path, text_path = _save_snapshot_files(source["id"], content_hash, result.body, text, is_pdf)
        repo.insert_snapshot(conn, source["id"], result.url, result.status, content_hash,
                             raw_path, text_path, selector)
    return "new_snapshot", result.status


def crawl(bank_code: str | None = None) -> dict:
    with session() as conn:
        sql = """SELECT s.* FROM sources s JOIN banks b ON b.id = s.bank_id WHERE s.enabled = 1"""
        params: tuple = ()
        if bank_code:
            sql += " AND b.code = ?"
            params = (bank_code,)
        sources = conn.execute(sql, params).fetchall()

    stats = {"new_snapshot": 0, "unchanged": 0, "error": 0,
             "health_warning": 0, "health_critical": 0}
    for src in sources:
        started = time.perf_counter()
        try:
            outcome, http_status = crawl_source(src)
            latency_ms = max(0, round((time.perf_counter() - started) * 1000))
            with session() as conn:
                repo.record_source_success(conn, src["id"], http_status, latency_ms)
            stats[outcome] += 1
            log.info("[%s] %s (%d ms)", outcome, src["url"], latency_ms)
        except Exception as e:
            latency_ms = max(0, round((time.perf_counter() - started) * 1000))
            http_status = getattr(e, "http_status", None)
            stats["error"] += 1
            with session() as conn:
                failures = repo.record_source_failure(
                    conn, src["id"], str(e), http_status, latency_ms
                )
            level = source_health.health_level(
                failures, enabled=True, last_attempt_at="attempted"
            )
            if level == "CRITICAL":
                stats["health_critical"] += 1
                log.error("[health:%s] %s failed %d consecutive times: %s",
                          level, src["url"], failures, e)
            elif level == "WARNING":
                stats["health_warning"] += 1
                log.warning("[health:%s] %s failed %d consecutive times: %s",
                            level, src["url"], failures, e)
            else:
                log.warning("[error:%s] %s: %s", level, src["url"], e)
        time.sleep(settings.REQUEST_DELAY_SECONDS)
    return stats


def discover(bank_code: str | None = None) -> int:
    """Rank links on official pages with AI and store promising ones as leads for review."""
    with session() as conn:
        sql = """SELECT s.*, b.code AS bank_code FROM sources s JOIN banks b ON b.id = s.bank_id
                 WHERE s.enabled = 1 AND s.source_type = 'official'"""
        params: tuple = ()
        if bank_code:
            sql += " AND b.code = ?"
            params = (bank_code,)
        sources = conn.execute(sql, params).fetchall()

    total = 0
    for src in sources:
        try:
            result = fetch(src["url"], src["fetcher"])
            if not result.html:
                continue
            domains = {ai_discovery.registrable_domain(src["url"])}
            leads = ai_discovery.discover_leads(result.html, src["url"], domains)
        except Exception as e:
            log.warning("discovery failed for %s: %s", src["url"], e)
            continue
        with session() as conn:
            # Compare canonical forms across both accepted sources and already-discovered leads so
            # rerunning discovery after URL-normalization changes does not create tracking-param duplicates.
            existing = {ai_discovery.canonicalize_url(r["url"]) for r in conn.execute("SELECT url FROM sources")}
            existing.update(
                ai_discovery.canonicalize_url(r["found_url"])
                for r in conn.execute("SELECT found_url FROM discovery_leads")
            )
            for lead in leads:
                lead_url = ai_discovery.canonicalize_url(lead["url"])
                if lead_url in existing:
                    continue
                inserted = repo.insert_lead(conn, src["bank_id"], lead_url, src["url"],
                                            lead.get("reason", ""), float(lead.get("score", 0)))
                total += int(inserted)
                if inserted:
                    existing.add(lead_url)
        time.sleep(settings.REQUEST_DELAY_SECONDS)
    return total


def _lead_fetcher(conn, lead) -> str:
    """Reuse the bank/domain's proven fetcher; PDFs stay on requests."""
    if lead["found_url"].lower().endswith(".pdf"):
        return "requests"
    target_domain = ai_discovery.registrable_domain(lead["found_url"])
    rows = conn.execute("SELECT url, fetcher FROM sources WHERE bank_id = ?", (lead["bank_id"],)).fetchall()
    matching = [r["fetcher"] for r in rows if ai_discovery.registrable_domain(r["url"]) == target_domain]
    return "playwright" if "playwright" in matching else "requests"


def validate_leads(bank_code: str | None = None, redo: bool = False, limit: int | None = None,
                   lead_id: int | None = None) -> dict:
    """Fetch pending discovery leads and turn high-recall candidates into recommendations."""
    with session() as conn:
        sql = """SELECT d.*, b.code AS bank_code, b.name AS bank_name
                   FROM discovery_leads d JOIN banks b ON b.id = d.bank_id
                  WHERE d.status = 'pending'"""
        params: list = []
        if bank_code:
            sql += " AND b.code = ?"
            params.append(bank_code)
        if lead_id:
            sql += " AND d.id = ?"
            params.append(lead_id)
        if not redo:
            sql += " AND COALESCE(d.validation_status, 'unvalidated') = 'unvalidated'"
        sql += " ORDER BY d.score DESC, d.id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        leads = [dict(r) for r in conn.execute(sql, params)]

    stats = {"validated": 0, "recommended": 0, "review": 0, "reject": 0, "fetch_failed": 0, "errors": 0}
    for lead in leads:
        prefiltered = content_validator.prefilter_url(lead["found_url"])
        if prefiltered:
            result = {**prefiltered, "validation_fetcher": None}
        else:
            with session() as conn:
                fetcher = _lead_fetcher(conn, lead)
            try:
                attempts = [fetcher]
                if not lead["found_url"].lower().endswith(".pdf"):
                    attempts.append("requests" if fetcher == "playwright" else "playwright")
                last_error = None
                for attempted_fetcher in dict.fromkeys(attempts):
                    try:
                        fetched = fetch(lead["found_url"], attempted_fetcher)
                        if fetched.status and fetched.status >= 400:
                            raise RuntimeError(f"HTTP {fetched.status}")
                        fetcher = attempted_fetcher
                        break
                    except Exception as e:
                        last_error = e
                else:
                    raise last_error or RuntimeError("all fetchers failed")
                is_pdf = "pdf" in fetched.content_type or lead["found_url"].lower().endswith(".pdf")
                if is_pdf:
                    text = pdf_to_text(fetched.body)
                else:
                    text = html_to_text(fetched.html or fetched.body.decode("utf-8", "replace"), None)
                if len(text) < MIN_TEXT_CHARS:
                    raise RuntimeError("page produced no usable text")
                result = content_validator.validate_page(
                    text, lead["found_url"], lead["bank_name"], lead.get("reason") or ""
                )
                result["validation_fetcher"] = fetcher
            except Exception as e:
                log.warning("lead validation failed for %s: %s", lead["found_url"], e)
                result = {
                    "validation_status": "fetch_failed",
                    "page_type": None,
                    "offer_type": None,
                    "offer_summary": None,
                    "content_score": 0.0,
                    "has_concrete_offer": None,
                    "has_eligibility": None,
                    "has_time_or_intro_period": None,
                    "is_stable_evidence_page": None,
                    "is_standing_feature": None,
                    "recommended_role": None,
                    "validation_reason": "fetch/content validation failed",
                    "evidence_quote": None,
                    "validation_confidence": None,
                    "validation_fetcher": fetcher,
                    "validation_error": str(e)[:500],
                }
        with session() as conn:
            repo.update_lead_validation(conn, lead["id"], result)
        stats["validated"] += 1
        status = result["validation_status"]
        if status in stats:
            stats[status] += 1
        else:
            stats["errors"] += 1
        if not prefiltered:
            time.sleep(settings.REQUEST_DELAY_SECONDS)
    return stats
