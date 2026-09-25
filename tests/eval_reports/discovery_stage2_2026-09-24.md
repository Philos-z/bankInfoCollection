# Discovery Stage-2 final acceptance report — 2026-09-24

## Scope

This report closes the discovery module from high-recall link discovery to evidence-bearing source recommendation.
The production AI model used for the live validation run is `gpt-5.6-sol`.

## Final pipeline

```text
official source page
  -> grounded link discovery (only real same-site links)
  -> URL canonicalization / duplicate suppression
  -> deterministic URL prefilter
  -> fetch HTML or PDF (requests / Playwright fallback)
  -> GPT-5.6 Sol content classification
  -> evidence-quote validation against fetched source text
  -> standing-feature exclusion
  -> deterministic recommendation policy
  -> recommended / review / reject
  -> human accept / reject gate
  -> accepted source_role = primary | supporting
```

AI does semantic classification; code owns the final recommendation policy.

## Production lead set

The first discovery run originally printed `90 new leads`. That exposed a counting bug: duplicate URLs discovered
from multiple official pages were counted even when SQLite ignored the duplicate insert. The bug was fixed.

- raw URL-unique persisted leads after the first run: 67
- one historical tracking-parameter duplicate remained (`inav` variant)
- after canonical identity cleanup: **66 canonical unique leads**
- historical lead URLs were normalized to remove `inav`, `linknav`, `intlink`, `wcmmode` and UTM-style tracking
  while keeping offer-selection parameters such as `offerId`/`productCode`.

## Final Stage-2 result

| Status | Count |
|---|---:|
| recommended | **33** |
| review | **2** |
| reject | **31** |
| total | **66** |

Recommended source roles:

- `primary`: **30**
- `supporting`: **3**

By bank:

| Bank | recommended | review | reject |
|---|---:|---:|---:|
| Advanzia | 3 | 0 | 1 |
| Amex DE | 14 | 2 | 12 |
| Crédit Agricole FR | 7 | 0 | 7 |
| HSBC UK | 9 | 0 | 11 |

The two `review` leads are Amex referral hubs whose public pages confirm referral mechanics but do not expose a
stable public reward amount; the actual offer is login/personal-link dependent.

## False-positive controls implemented

The second stage explicitly filters or downgrades:

- application/login/personal-data flows;
- generic legal/AGB/privacy pages;
- educational/explainer content;
- generic product pages without a concrete current offer;
- personalized offer hubs without stable public offer mechanics;
- permanent or annually recurring standing card benefits (e.g. included annual credits, permanent lounge
  entitlements, permanent paid points accelerators).

Acquisition, referral and introductory mechanics remain in scope even if continuously available.

## Evidence guard

A source cannot be auto-recommended unless its concrete offer can be traced back to fetched page text.

Evidence validation supports only controlled structural normalization:

- whitespace/case normalization;
- PDF word-spacing artifacts;
- machine-inserted `[@footnote]` markers;
- standalone 1-2 digit HTML footnote nodes;
- explicit `...`, `[...]`, `[…]` omissions only when all substantial quote fragments occur in source order.

There is no generic semantic/fuzzy-match bypass. If evidence cannot be grounded, confidence is capped and the lead
cannot become `recommended` automatically.

## Source-role semantics

`source_role` is now operational, not just metadata:

- `primary`: may seed a new campaign during validation;
- `supporting`: may attach evidence to an existing campaign but **cannot seed a new campaign**;
- when choosing the campaign display/primary extraction, a `primary`-role source outranks a `supporting` source,
  even if the supporting source is official and has higher model confidence.

This prevents a terms/summary-box source from creating standalone duplicate campaigns.

## Golden benchmark

`tests/fixtures/discovery_golden.json` contains **30 manually reviewed cases** spanning positive, negative and
boundary examples across Amex, HSBC, Advanzia and Crédit Agricole.

Final result:

- exact status + role accuracy: **100% (30/30)**
- recommendation precision on the golden set: **100%**
- recommendation recall on the golden set: **100%**
- mismatches: **0**

This is a curated regression benchmark, not a claim that unseen web pages have 100% real-world precision/recall.

## Regression / integration coverage

Final offline suite: **62 tests, all passing**.

Coverage includes:

- URL canonicalization and duplicate insert accounting;
- application/legal prefilters;
- content classification cleaning;
- standing-feature policy;
- evidence hallucination guard and HTML/PDF footnote artifacts;
- deterministic recommendation policy;
- primary/supporting source-role integration;
- extraction/schema and validator regressions;
- Amex entity-resolution integration.

## Operational verification

- `validate-leads` with no unvalidated leads returns all-zero stats (idempotent).
- CLI result counts match the database.
- `PRAGMA foreign_key_check` returns no violations.
- `PRAGMA integrity_check` returns `ok`.
- validation errors in the 66-lead set: **0**.

## Human gate

No discovery recommendation was automatically promoted into `sources` during this work. Human `leads --accept ID`
remains the final gate. Accepted leads carry their validated fetcher and recommended source role into `sources`.

