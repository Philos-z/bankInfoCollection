import sqlite3
from urllib.parse import urlparse

from storage.db import now_iso


def upsert_bank(conn: sqlite3.Connection, code: str, name: str, country: str, website: str) -> int:
    conn.execute(
        """INSERT INTO banks (code, name, country, website) VALUES (?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, country=excluded.country,
                                           website=excluded.website""",
        (code, name, country, website),
    )
    return conn.execute("SELECT id FROM banks WHERE code = ?", (code,)).fetchone()["id"]


def upsert_source(conn, bank_id: int, source_type: str, url: str, fetcher: str,
                  selector: str | None, source_role: str = "primary") -> int:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    conn.execute(
        """INSERT INTO sources (bank_id, source_type, url, domain, fetcher, source_role, selector)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET source_type=excluded.source_type,
               fetcher=excluded.fetcher,
               source_role=excluded.source_role,
               selector=COALESCE(sources.selector, excluded.selector)""",
        (bank_id, source_type, url, domain, fetcher, source_role, selector),
    )
    return conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()["id"]


def get_or_create_card(conn, bank_id: int, name: str | None) -> int | None:
    if not name:
        return None
    conn.execute("INSERT OR IGNORE INTO cards (bank_id, name) VALUES (?, ?)", (bank_id, name.strip()))
    return conn.execute(
        "SELECT id FROM cards WHERE bank_id = ? AND name = ?", (bank_id, name.strip())
    ).fetchone()["id"]


def latest_snapshot_hash(conn, source_id: int) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM raw_snapshots WHERE source_id = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    return row["content_hash"] if row else None


def record_source_success(conn, source_id: int, http_status: int | None, latency_ms: int) -> None:
    """Record a successful source attempt and clear the consecutive-failure streak."""
    ts = now_iso()
    conn.execute(
        """UPDATE sources
              SET last_attempt_at = ?, last_success_at = ?, consecutive_failures = 0,
                  last_http_status = ?, last_latency_ms = ?, last_error = NULL
            WHERE id = ?""",
        (ts, ts, http_status, latency_ms, source_id),
    )


def record_source_failure(conn, source_id: int, error: str, http_status: int | None,
                          latency_ms: int) -> int:
    """Record a failed attempt and return the updated consecutive-failure count."""
    conn.execute(
        """UPDATE sources
              SET last_attempt_at = ?,
                  consecutive_failures = COALESCE(consecutive_failures, 0) + 1,
                  last_http_status = ?, last_latency_ms = ?, last_error = ?
            WHERE id = ?""",
        (now_iso(), http_status, latency_ms, error[:500], source_id),
    )
    row = conn.execute(
        "SELECT consecutive_failures FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    return int(row["consecutive_failures"])


def insert_snapshot(conn, source_id: int, url: str, http_status: int | None, content_hash: str,
                    raw_path: str, text_path: str, selector_used: str | None) -> int:
    cur = conn.execute(
        """INSERT INTO raw_snapshots (source_id, url, fetched_at, http_status, content_hash,
                                      raw_content_path, text_content_path, selector_used)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_id, url, now_iso(), http_status, content_hash, raw_path, text_path, selector_used),
    )
    return cur.lastrowid


def insert_lead(conn, bank_id: int, url: str, from_url: str, reason: str, score: float) -> bool:
    cur = conn.execute(
        """INSERT OR IGNORE INTO discovery_leads (bank_id, found_url, from_url, reason, score, found_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (bank_id, url, from_url, reason, score, now_iso()),
    )
    if cur.rowcount == 0:
        conn.execute(
            """UPDATE discovery_leads
                  SET score = CASE WHEN ? > COALESCE(score, 0) THEN ? ELSE score END,
                      reason = CASE WHEN ? > COALESCE(score, 0) THEN ? ELSE reason END,
                      from_url = CASE WHEN ? > COALESCE(score, 0) THEN ? ELSE from_url END
                WHERE bank_id = ? AND found_url = ?""",
            (score, score, score, reason, score, from_url, bank_id, url),
        )
    return cur.rowcount > 0


def update_lead_validation(conn, lead_id: int, result: dict) -> None:
    conn.execute(
        """UPDATE discovery_leads
              SET validation_status = ?, page_type = ?, offer_type = ?, offer_summary = ?, content_score = ?,
                  has_concrete_offer = ?, has_eligibility = ?, has_time_or_intro_period = ?,
                  is_stable_evidence_page = ?, is_standing_feature = ?, recommended_role = ?, validation_reason = ?,
                  evidence_quote = ?, validation_confidence = ?, validation_fetcher = ?,
                  validation_error = ?, validated_at = ?
            WHERE id = ?""",
        (
            result.get("validation_status", "review"), result.get("page_type"), result.get("offer_type"),
            result.get("offer_summary"), result.get("content_score"),
            int(bool(result.get("has_concrete_offer"))) if result.get("has_concrete_offer") is not None else None,
            int(bool(result.get("has_eligibility"))) if result.get("has_eligibility") is not None else None,
            int(bool(result.get("has_time_or_intro_period"))) if result.get("has_time_or_intro_period") is not None else None,
            int(bool(result.get("is_stable_evidence_page"))) if result.get("is_stable_evidence_page") is not None else None,
            int(bool(result.get("is_standing_feature"))) if result.get("is_standing_feature") is not None else None,
            result.get("recommended_role"), result.get("validation_reason"), result.get("evidence_quote"),
            result.get("validation_confidence"), result.get("validation_fetcher"), result.get("validation_error"),
            result.get("validated_at", now_iso()), lead_id,
        ),
    )
