import yaml

import settings
from storage import repo
from storage.db import session


def sync_config() -> int:
    """Upsert banks and sources from banks.yaml; returns number of sources."""
    data = yaml.safe_load(settings.BANKS_CONFIG.read_text(encoding="utf-8"))
    count = 0
    with session() as conn:
        for bank in data["banks"]:
            bank_id = repo.upsert_bank(conn, bank["code"], bank["name"], bank["country"], bank.get("website"))
            for src in bank.get("sources", []):
                source_id = repo.upsert_source(conn, bank_id, src["type"], src["url"],
                                               src.get("fetcher", "requests"), src.get("selector"),
                                               src.get("role", "primary"))
                conn.execute("UPDATE sources SET enabled = ? WHERE id = ?",
                             (1 if src.get("enabled", True) else 0, source_id))
                count += 1
    return count
