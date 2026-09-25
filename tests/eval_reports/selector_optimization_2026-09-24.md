# CSS selector optimization acceptance — 2026-09-24

## Goal

Reduce full-page noise/token volume on high-cost or high-noise sources without losing campaign evidence.
Selectors were chosen from stored HTML first, checked against existing `evidence_quote` coverage, then validated
with live fetch + GPT-5.6 Sol extraction before being accepted.

## Accepted selectors

| Source | Selector | Before chars | After chars | Reduction |
|---|---|---:|---:|---:|
| Amex official welcome-bonus article | `main` | 34,645 | 18,289 | 47% |
| MoneySavingExpert balance-transfer guide | `article` | 49,978 | 39,832 | 20% |
| HelpMyCash BBVA card page | `main` | 16,766 | 7,038 | 58% |
| Crédit Agricole card overview | `main#content` | 8,922 | 7,248 | 19% |
| Crédit Agricole account/card comparison | `main#content` | 13,408 | 11,727 | 13% |

At the initial selector pass Advanzia was intentionally left without a selector because the first candidates either
saved little text or lost current evidence. A later V1 idempotency run exposed that its application form sometimes
expanded hundreds of dropdown values into the DOM. A narrower evidence-preserving marketing selector was then
identified and accepted; see the follow-up below.

## Live validation

All five selectors matched the live DOM and produced new snapshots without selector fallback or fetch errors.

The selector snapshots were then extracted and validated end-to-end.

### Amex

The previous full-page official snapshot produced 14 extraction rows, including duplicated page content. The
selector-based snapshot now produces 8 clean offer records while preserving the important campaigns:

- Platinum up to 85k
- Gold up to 50k
- Gold Rosé up to 50k
- BMW Premium Card Carbon 37.5k
- BMW Card 11k
- Blue Card combined welcome offer (25 EUR start credit + optional 5k ExtraPunkte)
- PAYBACK 1k
- American Express Card referral 10k/10k

All 8 linked to existing campaign entities; **0 new campaign entities** were created.

The evidence guard was tightened at the same time: explicit `[...]` omissions are accepted only when all
substantial fragments exist in source order. This restored genuine Amex evidence confidence to 0.96–0.98 while
still rejecting hallucinated fragments.

Known Amex naming continuity was also fixed: `BMW Premium Card Carbon` and `BMW Card Carbon` normalize to the
same card identity. The historical campaign id was preserved rather than creating a new selector-era entity.

### HSBC / MSE

The MSE selector retains the concrete 36-month 0% balance-transfer evidence. Its extraction attaches to the
existing HSBC Balance Transfer campaign through deterministic offer-mechanics matching even when `card_name` is
missing.

### Crédit Agricole

The two official selectors preserve the umbrella 100 EUR welcome offer and the two distinct 50 EUR components.
Moneyvox's EKO/Essentiel/Premium/Prestige package variants are now collapsed to one `card_bundle` extraction and
attach to the verified umbrella campaign.

Final CA structure remains:

- 50 EUR account-opening component — unverified, official only
- up to 100 EUR umbrella welcome campaign — verified by official + Selectra + Moneyvox
- 50 EUR bank-mobility component — unverified, official only

## Final checks

- no selector fetch fallback on the five accepted sources
- 3 Amex core campaigns remain verified
- CA umbrella campaign remains verified
- HSBC 36-month balance-transfer campaign remains verified
- no conflicts introduced
- SQLite foreign-key check clean
- SQLite integrity check `ok`
- offline regression suite: **75 tests passing**

## V1 idempotency follow-up — 2026-09-25

Three additional selectors were introduced after repeated full-run idempotency checks exposed dynamic non-business
page regions:

| Source | Selector | Purpose |
|---|---|---|
| Moneyvox Crédit Agricole | `.fiche-produit` | exclude rotating comparison/news modules |
| Reisetopia Amex Gold | `.main-content > article` | exclude rotating related-posts/relative-time module |
| Advanzia official | `.adt-banner-promo.adt-active, .adt-promotion-container-omnichannel, .adt-avis-container-omnichannel` | exclude dynamic application-form UI while retaining referral + AVIS evidence |

For Moneyvox and Reisetopia, the selected content was byte-for-byte stable across multiple previously different
snapshots and retained all current evidence. For Advanzia, the selector produced identical text across both the
collapsed and expanded application-form DOM states and retained both current campaign evidence quotes.

Each source then passed a live two-fetch acceptance: first fetch created the selector-based snapshot, second fetch
returned `unchanged`.

