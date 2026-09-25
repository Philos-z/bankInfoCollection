# Amex production rebuild — 2026-09-23

This report records the first production-data rebuild after the Amex entity-resolution fix.

## Safety

Before rebuilding, the SQLite database was copied to:

`data/backups/bank_campaigns_before_amex_rebuild_2026-09-23.db`

Only `amex_de` extractions/campaigns were rebuilt.

## Before

- extractions: 28
- campaigns: 22
- verified: 3
- unverified: 19
- conflict: 0
- null-card extractions: 0

Known duplicate campaigns included:

- Blue Card 25 EUR start credit
- Blue Card 5,000 points
- PAYBACK 1,000 points
- BMW Carbon 37,500 points
- BMW Card 11,000 points

## Re-extraction with GPT-5.6 Sol

- snapshots: 5/5 successful
- extractions: 29
- extraction errors: 0
- `card_name = null`: 0
- Gold and Gold Rosé emitted as separate card records

## Rebuild result

Validator result:

- linked extractions: 12
- created canonical campaigns: 17
- AI matches: 2

Final Amex state:

- campaigns: **17** (down from 22)
- verified: **3** (unchanged)
- unverified: **14** (down from 19)
- conflict: **0**
- extractions: 29
- null-card extractions: 0

The reduction from 22 to 17 is exactly the five known duplicate groups listed above.

## Critical entity checks

- Platinum 85k: retained, 3 supporting extractions, `verified`.
- Gold 50k: retained, 3 supporting extractions, `verified`.
- Gold Rosé 50k: retained separately, 4 supporting extractions, `verified`.
- Blue 25 EUR: two same-page extractions collapsed to one campaign.
- Blue 5k: two same-page extractions collapsed to one campaign.
- PAYBACK 1k: two same-page extractions collapsed to one campaign.
- BMW Card 11k: two same-page extractions collapsed to one campaign.
- BMW Carbon 37.5k: `BMW Premium Card Carbon` + `BMW Card Carbon` merged through AI arbitration.

No evidence was found that the five-campaign reduction removed a distinct known promotion.

