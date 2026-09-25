# V1 System Acceptance — 2026-09-25

## Scope

This acceptance closes V1 feature development for the European bank card-promotion collector.
The system was exercised through the real production path with live sources and GPT-5.6 Sol, while keeping
human acceptance as the final gate for newly discovered sources.

## Final architecture exercised

```text
config/banks.yaml
  -> crawl / source health / snapshots
  -> GPT extraction / evidence grounding
  -> entity resolution / source roles
  -> verification / lifecycle
  -> discovery / content validation / human gate
  -> SQLite canonical campaigns
```

## Idempotency acceptance

The first full V1 run encountered real page changes and correctly processed them without campaign inflation:

- crawl: 4 new snapshots / 17 unchanged / 0 errors
- extract: 4 snapshots / 12 extraction rows / 0 errors
- validate: 12 linked / 0 created

Repeated runs then exposed three non-business DOM-noise sources:

- Moneyvox rotating comparison/news modules
- Reisetopia rotating “similar posts” and relative-time labels
- Advanzia application-form dropdown/select UI

These were fixed through source-level CSS selectors, not bank-specific business logic:

- Moneyvox: `.fiche-produit`
- Reisetopia: `.main-content > article`
- Advanzia: `.adt-banner-promo.adt-active, .adt-promotion-container-omnichannel, .adt-avis-container-omnichannel`

For each source, the selector was validated against multiple stored DOM states and current evidence before being
enabled. Each selector then passed a live `new_snapshot -> unchanged` two-fetch check.

The final full production run was fully idempotent:

- crawl: **0 new snapshots / 21 unchanged / 0 errors**
- extract: **0 snapshots / 0 campaigns / 0 errors**
- validate: **0 linked / 0 created / 0 AI matches**

## Proxy resilience

The AI client now retries only retryable transport/server failures:

- connection errors
- timeouts
- rate limits
- HTTP 5xx

Default policy:

- `AI_HTTP_RETRIES=2` (3 total attempts)
- exponential backoff from `AI_RETRY_BASE_SECONDS=2`
- `AI_MAX_CONCURRENCY=1`

Non-retryable client errors are surfaced immediately. JSON-format retries remain separate from transport retries.

## Lifecycle robustness

System acceptance exposed a generic lifecycle failure mode: an LLM extraction pass may omit a still-visible offer,
which previously could mark the canonical campaign as `removed`.

V1 now uses a presence guard. When a campaign has no current extraction, its previously grounded evidence is
checked against the latest extracted snapshot of enabled prior supporting sources:

- evidence still present -> preserve current lifecycle and refresh `last_seen_at`
- evidence absent -> allow `removed`
- known end date already expired -> `expired`

This restored three Amex campaigns that were still visibly present but transiently missed by the latest extraction.
The rule is fully source/campaign generic and contains no bank-specific knowledge.

## Incremental Discovery acceptance

Baseline before the final incremental run:

- 66 validated canonical leads
- 0 unvalidated leads

Incremental `discover` found **9 new canonical leads**.

Stage-2 validation classified them as:

- recommended: 1
- reject: 7
- fetch_failed: 1

The failed page belonged to the same application-flow family as successfully validated sibling pages and was
closed through the human reject gate rather than adding a site-specific production rule.

Final automated backlog:

- pending snapshots: **0**
- pending unvalidated discovery leads: **0**
- pending fetch_failed discovery leads: **0**

Human-gate states remain intentionally separate from automation backlog. Current discovery totals:

- total leads: **75**
- pending recommended: **34**
- pending review: **2**
- validator reject: **38**
- human-rejected fetch failure: **1**

No newly recommended source was auto-accepted into production sources.

## Final source health

- sources: **24**
- enabled: **21**
- health OK: **21**
- degraded/warning/critical/unknown: **0**
- disabled: **3**

The disabled sources are deliberate inaccessible/historical BBVA sources rather than current crawler failures.

## Final campaign state

| Bank | Campaigns | Verified | Unverified | Conflict | Removed |
|---|---:|---:|---:|---:|---:|
| Advanzia | 5 | 1 | 4 | 0 | 0 |
| Amex DE | 16 | 3 | 13 | 0 | 0 |
| BBVA ES | 2 | 1 | 1 | 0 | 0 |
| Crédit Agricole FR | 3 | 1 | 2 | 0 | 0 |
| HSBC UK | 7 | 1 | 6 | 0 | 0 |

Total canonical campaigns: **33**. Verified: **7**. Conflicts: **0**. Removed: **0**.

A generic exact-card/reward duplicate audit found **0 suspicious active duplicate pairs**.

## Discovery regression benchmark

The manually reviewed 30-case discovery golden set remains unchanged after all V1 work:

- exact status + role accuracy: **100%**
- recommendation precision: **100%**
- recommendation recall: **100%**
- mismatches: **0**

These percentages describe the curated golden set, not unseen-page real-world precision/recall.

## Database / regression acceptance

- pending snapshots: **0**
- SQLite foreign-key check: clean
- SQLite integrity check: `ok`
- offline regression suite: **80 tests, all passing**
- production bank/product literal guard: clean

## V1 decision

V1 feature development is accepted as complete. Future work should be maintenance-oriented:

- add banks/sources primarily through configuration;
- add regression cases only when real runs expose new generic boundaries;
- periodically review recommended discovery leads through the human gate;
- rotate external proxy credentials when required;
- optionally add external alert delivery (mail/Slack) on top of the existing health states.

