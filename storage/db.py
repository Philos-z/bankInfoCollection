import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS banks (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    country     TEXT NOT NULL,
    website     TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,
    bank_id     INTEGER NOT NULL REFERENCES banks(id),
    name        TEXT NOT NULL,
    url         TEXT,
    UNIQUE (bank_id, name)
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    bank_id      INTEGER NOT NULL REFERENCES banks(id),
    source_type  TEXT NOT NULL CHECK (source_type IN ('official', 'third_party', 'discovered')),
    url          TEXT NOT NULL UNIQUE,
    domain       TEXT NOT NULL,
    fetcher      TEXT NOT NULL DEFAULT 'requests' CHECK (fetcher IN ('requests', 'playwright')),
    source_role  TEXT NOT NULL DEFAULT 'primary' CHECK (source_role IN ('primary', 'supporting')),
    selector     TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_error   TEXT,
    last_attempt_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_http_status INTEGER,
    last_latency_ms INTEGER
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    id                INTEGER PRIMARY KEY,
    source_id         INTEGER NOT NULL REFERENCES sources(id),
    url               TEXT NOT NULL,
    fetched_at        TEXT NOT NULL,
    http_status       INTEGER,
    content_hash      TEXT NOT NULL,
    raw_content_path  TEXT NOT NULL,
    text_content_path TEXT NOT NULL,
    selector_used     TEXT,
    extracted         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_snapshots_source ON raw_snapshots(source_id, fetched_at);

CREATE TABLE IF NOT EXISTS campaign_extractions (
    id                 INTEGER PRIMARY KEY,
    snapshot_id        INTEGER NOT NULL REFERENCES raw_snapshots(id),
    bank_id            INTEGER NOT NULL REFERENCES banks(id),
    card_id            INTEGER REFERENCES cards(id),
    product_scope      TEXT NOT NULL DEFAULT 'credit_card' CHECK (product_scope IN ('credit_card', 'card_bundle')),
    campaign_title     TEXT NOT NULL,
    campaign_type      TEXT NOT NULL,
    offer_summary      TEXT,
    reward_value       TEXT,
    conditions         TEXT,
    start_date         TEXT,
    end_date           TEXT,
    language           TEXT,
    evidence_quote     TEXT,
    confidence         REAL,
    extraction_method  TEXT NOT NULL,
    extracted_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id                     INTEGER PRIMARY KEY,
    bank_id                INTEGER NOT NULL REFERENCES banks(id),
    card_id                INTEGER REFERENCES cards(id),
    product_scope          TEXT NOT NULL DEFAULT 'credit_card' CHECK (product_scope IN ('credit_card', 'card_bundle')),
    canonical_title        TEXT NOT NULL,
    campaign_type          TEXT NOT NULL,
    offer_summary          TEXT,
    reward_value           TEXT,
    conditions             TEXT,
    start_date             TEXT,
    end_date               TEXT,
    verification_status    TEXT NOT NULL DEFAULT 'unverified'
        CHECK (verification_status IN ('verified', 'unverified', 'conflict')),
    lifecycle_status       TEXT NOT NULL DEFAULT 'active'
        CHECK (lifecycle_status IN ('upcoming', 'active', 'expiring', 'expired', 'removed')),
    primary_extraction_id  INTEGER REFERENCES campaign_extractions(id),
    verification_notes     TEXT,
    first_seen_at          TEXT NOT NULL,
    last_seen_at           TEXT NOT NULL,
    last_verified_at       TEXT
);

CREATE TABLE IF NOT EXISTS campaign_sources (
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id),
    extraction_id  INTEGER NOT NULL REFERENCES campaign_extractions(id),
    match_method   TEXT NOT NULL CHECK (match_method IN ('seed', 'rule', 'ai')),
    PRIMARY KEY (campaign_id, extraction_id)
);

CREATE TABLE IF NOT EXISTS campaign_enrichments (
    campaign_id     INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
    input_hash      TEXT NOT NULL,
    model           TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('complete', 'error')),
    generated_at    TEXT NOT NULL,
    raw_json        TEXT,
    error           TEXT
);

CREATE TABLE IF NOT EXISTS campaign_facts (
    id              INTEGER PRIMARY KEY,
    campaign_id     INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    fact_type       TEXT NOT NULL CHECK (fact_type IN (
        'headline', 'maximum_reward', 'eligibility', 'required_step', 'action_reward',
        'optional_step', 'deadline', 'reward_period', 'exclusion', 'warning'
    )),
    fact_key        TEXT,
    position        INTEGER NOT NULL DEFAULT 0,
    value_json      TEXT NOT NULL,
    confidence      REAL,
    generated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaign_facts_campaign ON campaign_facts(campaign_id, fact_type, position);

CREATE TABLE IF NOT EXISTS fact_evidence (
    fact_id         INTEGER NOT NULL REFERENCES campaign_facts(id) ON DELETE CASCADE,
    extraction_id   INTEGER NOT NULL REFERENCES campaign_extractions(id),
    snapshot_id     INTEGER NOT NULL REFERENCES raw_snapshots(id),
    PRIMARY KEY (fact_id, extraction_id)
);
CREATE INDEX IF NOT EXISTS idx_fact_evidence_extraction ON fact_evidence(extraction_id);

CREATE TABLE IF NOT EXISTS discovery_leads (
    id          INTEGER PRIMARY KEY,
    bank_id     INTEGER NOT NULL REFERENCES banks(id),
    found_url   TEXT NOT NULL,
    from_url    TEXT,
    reason      TEXT,
    score       REAL,
    validation_status TEXT NOT NULL DEFAULT 'unvalidated',
    page_type          TEXT,
    offer_type         TEXT,
    offer_summary      TEXT,
    content_score      REAL,
    has_concrete_offer INTEGER,
    has_eligibility    INTEGER,
    has_time_or_intro_period INTEGER,
    is_stable_evidence_page INTEGER,
    is_standing_feature INTEGER,
    recommended_role   TEXT,
    validation_reason  TEXT,
    evidence_quote     TEXT,
    validation_confidence REAL,
    validation_fetcher TEXT,
    validation_error   TEXT,
    validated_at       TEXT,
    status      TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected')),
    found_at    TEXT NOT NULL,
    UNIQUE (bank_id, found_url)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release; CREATE TABLE IF NOT EXISTS won't add them to existing DBs.
MIGRATIONS = [
    ("campaign_extractions", "product_scope",
     "TEXT NOT NULL DEFAULT 'credit_card' CHECK (product_scope IN ('credit_card', 'card_bundle'))"),
    ("campaigns", "product_scope",
     "TEXT NOT NULL DEFAULT 'credit_card' CHECK (product_scope IN ('credit_card', 'card_bundle'))"),
    ("sources", "source_role",
     "TEXT NOT NULL DEFAULT 'primary' CHECK (source_role IN ('primary', 'supporting'))"),
    ("sources", "last_attempt_at", "TEXT"),
    ("sources", "last_success_at", "TEXT"),
    ("sources", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("sources", "last_http_status", "INTEGER"),
    ("sources", "last_latency_ms", "INTEGER"),
    ("discovery_leads", "validation_status", "TEXT NOT NULL DEFAULT 'unvalidated'"),
    ("discovery_leads", "page_type", "TEXT"),
    ("discovery_leads", "offer_type", "TEXT"),
    ("discovery_leads", "offer_summary", "TEXT"),
    ("discovery_leads", "content_score", "REAL"),
    ("discovery_leads", "has_concrete_offer", "INTEGER"),
    ("discovery_leads", "has_eligibility", "INTEGER"),
    ("discovery_leads", "has_time_or_intro_period", "INTEGER"),
    ("discovery_leads", "is_stable_evidence_page", "INTEGER"),
    ("discovery_leads", "is_standing_feature", "INTEGER"),
    ("discovery_leads", "recommended_role", "TEXT"),
    ("discovery_leads", "validation_reason", "TEXT"),
    ("discovery_leads", "evidence_quote", "TEXT"),
    ("discovery_leads", "validation_confidence", "REAL"),
    ("discovery_leads", "validation_fetcher", "TEXT"),
    ("discovery_leads", "validation_error", "TEXT"),
    ("discovery_leads", "validated_at", "TEXT"),
]


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
