# BBVA config-only onboarding acceptance — 2026-09-24

## Goal

Use BBVA as a proof that a new/under-covered bank can be improved through source configuration and data only,
without adding bank-specific branches to the generic Python engine.

## Source discovery

The existing official `bbva.es` pages remain blocked by Akamai in the local crawler (HTTP 403), so the official
sources stay disabled rather than attempting to bypass bot protection.

Three accessible current third-party sources were added through `config/banks.yaml` only:

- Finect
- iFinanzas
- Roams

All three are independently hosted domains and currently describe the same PLAN1060 account/card-bundle offer.

The previously enabled elEconomista source was disabled after repeated HTTP 403 and retained only for historical
traceability.

## Pre-ingestion validation

Before adding them to production config, all three candidate pages were tested with the project's own
`requests -> html_to_text -> GPT-5.6 Sol -> clean_campaign` path without writing to the database.

All three independently produced:

- `product_scope = card_bundle`
- `campaign_type = welcome_bonus`
- `card_name = null`
- reward = up to 1,060 EUR
- `end_date = 2026-10-20`
- confidence 0.96–0.98

## Production ingestion

All three sources fetched successfully with the standard `requests` fetcher:

- Finect: HTTP 200
- iFinanzas: HTTP 200
- Roams: HTTP 200

The three snapshots produced exactly three extraction rows and no extraction errors.

Validation result:

- `created = 1`
- `linked = 2`
- no bank-specific code changes

Final canonical campaign:

- product scope: `card_bundle`
- campaign type: `welcome_bonus`
- reward: up to 1,060 EUR
- start date: 2026-06-09 (from the source that states it)
- end date: 2026-10-20
- verification status: **verified**
- independent domains: **finect.com, ifinanzas.es, roams.es**

## Config / health acceptance

After disabling the inaccessible elEconomista source, a full BBVA crawl was run through normal bank-scoped
configuration.

Result:

- enabled sources: 4
- unchanged: 4
- errors: 0
- health: 4 OK / 3 DISABLED / 0 degraded-warning-critical

The disabled sources are the two inaccessible official pages plus the historical elEconomista page.

## Generality proof

The onboarding required changes only to `config/banks.yaml` plus normal persisted data. No extraction,
entity-resolution, verification, discovery, health or database business rule was modified for BBVA.

The generic engine therefore handled a new bank campaign through the same normalized schema and verification
pipeline used for all other banks.

## Integrity

- offline suite: 76 tests passing
- pending snapshots: 0
- SQLite foreign-key check: clean
- SQLite integrity check: `ok`

