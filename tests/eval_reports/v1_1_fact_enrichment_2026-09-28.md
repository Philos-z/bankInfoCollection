# V1.1 Fact Enrichment acceptance — 2026-09-28

## Goal

Turn canonical bank campaigns into an action-oriented user model:

- what the user can get;
- eligibility;
- required first steps;
- action / requirement -> reward mapping;
- optional steps;
- deadline and reward period;
- exclusions and warnings;
- field-level evidence references.

The enrichment must be persisted and cached; normal `report` calls must not require GPT.

## Persistence model

Three tables were added:

- `campaign_enrichments`: campaign input hash, model, completion/error status, generation time, raw GPT JSON;
- `campaign_facts`: typed structured facts with deterministic position/key/value/confidence;
- `fact_evidence`: each fact's extraction and snapshot provenance.

## Grounding / safety rules

- every fact must cite one or more extraction IDs;
- cited extraction IDs must belong to that canonical campaign;
- amounts, thresholds, dates and durations in the generated fact are normalized and checked against the cited
  extraction fields/evidence before persistence;
- if campaign or supporting extraction input changes, the stored input hash becomes stale and Report falls back
  instead of presenting the previous action plan;
- cached facts are returned without a new model call when the input hash is unchanged.

## Live verified-campaign run

All seven current cross-source verified campaigns were enriched successfully using GPT-5.6 Sol:

| Campaign ID | Domain | Persisted facts |
|---:|---|---:|
| 25 | Advanzia referral | 11 |
| 62 | HSBC balance transfer | 11 |
| 70 | Crédit Agricole welcome offer | 14 |
| 72 | Amex Platinum | 13 |
| 73 | Amex Gold | 14 |
| 78 | Amex Gold Rosé | 13 |
| 88 | BBVA PLAN1060 | 20 |

Final local database state after the acceptance run:

- campaign enrichments: **7 complete / 0 error**
- campaign facts: **96**
- fact-evidence links: **196**
- SQLite foreign-key check: clean
- SQLite integrity: `ok`

A second `enrich --verified-only` run returned **7 cached / 0 model calls required by the enrichment pipeline**.

## BBVA action-plan example

The report now renders campaign 88 as an action plan, including:

- new-customer / age / former-customer / sole-owner eligibility;
- opening the account with code PLAN1060 as the required first step;
- salary >= EUR 800 -> EUR 400;
- qualifying debit-card use -> EUR 120;
- qualifying Bizum use -> EUR 120;
- qualifying direct debits -> EUR 120;
- average balance >= EUR 10,000 -> EUR 300;
- application deadline 20 October 2026;
- first-year / 12-month reward period;
- exclusions and gross/net warnings.

Each eligibility/action row includes evidence domains and the JSON representation includes the precise
extraction/snapshot refs.

## Regression

- final offline suite: **91 / 91 tests passed**
- compile/import checks passed
- existing V1 crawl/extract/validate/report regressions remain green

Runtime enrichments live in the local SQLite database under `data/`, which is intentionally Git-ignored. The
schema, enrichment pipeline, evidence guards, tests and documentation are version controlled.

