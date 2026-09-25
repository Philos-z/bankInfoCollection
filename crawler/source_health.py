"""Source-health classification shared by crawler logging and the health CLI."""

from __future__ import annotations

import settings


def health_level(consecutive_failures: int | None, *, enabled: bool = True,
                 last_attempt_at: str | None = None) -> str:
    if not enabled:
        return "DISABLED"
    if last_attempt_at is None:
        return "UNKNOWN"
    failures = int(consecutive_failures or 0)
    if failures >= settings.SOURCE_HEALTH_CRITICAL_FAILURES:
        return "CRITICAL"
    if failures >= settings.SOURCE_HEALTH_WARNING_FAILURES:
        return "WARNING"
    if failures > 0:
        return "DEGRADED"
    return "OK"


def counts(rows) -> dict[str, int]:
    result = {k: 0 for k in ("OK", "DEGRADED", "WARNING", "CRITICAL", "UNKNOWN", "DISABLED")}
    for row in rows:
        level = health_level(
            row["consecutive_failures"],
            enabled=bool(row["enabled"]),
            last_attempt_at=row["last_attempt_at"],
        )
        result[level] += 1
    return result
