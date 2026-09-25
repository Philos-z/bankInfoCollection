# Source Health acceptance — 2026-09-24

## Implemented

Each source now persists:

- `last_attempt_at`
- `last_success_at`
- `consecutive_failures`
- `last_http_status`
- `last_latency_ms`
- existing `last_error`

Health levels for enabled sources:

- `UNKNOWN`: no health-aware crawl attempt recorded yet
- `OK`: 0 consecutive failures
- `DEGRADED`: 1-2 consecutive failures
- `WARNING`: 3-4 consecutive failures
- `CRITICAL`: 5+ consecutive failures

Disabled sources are reported as `DISABLED`.

A successful crawl resets the failure streak to zero. Failed attempts increment it and preserve the last error,
HTTP status when available, and latency. Crawl logs explicitly escalate WARNING/CRITICAL states. Thresholds are
configurable via `SOURCE_HEALTH_WARNING_FAILURES` and `SOURCE_HEALTH_CRITICAL_FAILURES`.

## CLI

```bash
.venv/bin/python main.py health
.venv/bin/python main.py health --bank amex_de
```

`sources` also includes the persisted health fields for machine-readable inspection.

## Live acceptance run

A full live crawl of all 19 enabled sources was executed after migration.

Result:

- unchanged: 15
- new snapshots: 3
- errors: 1
- warning threshold hits: 0
- critical threshold hits: 0

Final source-health summary:

- `OK`: **18**
- `DEGRADED`: **1**
- `WARNING`: **0**
- `CRITICAL`: **0**
- `UNKNOWN`: **0**
- `DISABLED`: **2**

The one degraded source is the already-known elEconomista BBVA third-party page, which returned HTTP 403. It is
now recorded as:

- consecutive failures: 1
- last HTTP status: 403
- last error: `HTTP 403`
- last latency: 3110 ms

All other enabled sources recorded successful attempts and HTTP 200.

## Integrity / regression

- all 19 enabled sources have a recorded health-aware attempt
- 18 have a recorded success and zero failure streak
- SQLite foreign-key check: clean
- SQLite integrity check: `ok`
- offline suite after the feature: **64 tests, all passing**

