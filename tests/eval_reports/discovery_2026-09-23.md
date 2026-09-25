# AI discovery evaluation — 2026-09-23

## Run

- model: `gpt-5.6-sol`
- command: `.venv/bin/python main.py discover`
- starting leads: 0
- command initially reported: 90 new leads
- actually persisted unique leads: **67**

The 90/67 mismatch exposed a counting bug: the same candidate URL can be discovered from more than one official
page. `INSERT OR IGNORE` correctly prevented duplicate database rows, but `discover()` incremented its counter even
when the insert was ignored. This was fixed so the reported count now reflects newly inserted unique rows.

## Unique leads by bank

| Bank | Unique leads | Average score |
|---|---:|---:|
| Amex DE | 29 | 0.859 |
| HSBC UK | 20 | 0.840 |
| Crédit Agricole FR | 14 | 0.601 |
| Advanzia | 4 | 0.833 |

Score distribution among the 67 unique leads:

- `>= 0.90`: 25
- `>= 0.80`: 39
- lower than `0.80`: 28

## Manual/content audit

22 representative candidates were fetched directly and inspected (high-score candidates plus suspicious and
lower-score controls). Main findings:

### Strong / actionable discovery examples

- Advanzia `/sonderaktionen`: dedicated special-offers page; requests receives Cloudflare 403, so it needs
  Playwright like the existing Advanzia official source.
- Advanzia `/buyapowa`: referral campaign page; also needs Playwright because requests receives 403.
- Amex referral page: live official referral campaign with bonus-points language.
- HSBC Balance Transfer Summary Box PDF: contains `0%` balance transfer for up to 36 months plus fees/conditions.
- HSBC Purchase Plus Summary Box PDF: contains `0%` purchases up to 24 months and balance-transfer terms.
- HSBC Rewards Credit Card page: explicit 2,500-point welcome bonus and introductory 0% periods.
- Crédit Agricole `/ouvrir-un-compte.html`: explicit current `jusqu'à 100€ offerts` account/card-bundle offer.
- Crédit Agricole Prestige page: repeats the current up-to-100€ opening offer with package context.

### Useful evidence, but not necessarily a primary crawl source

- Amex Platinum/Gold `angebotsbedingungen` pages contain detailed card-credit/merchant-offer conditions.
  They are useful for official evidence, but can create many long-lived benefit records rather than acquisition
  campaigns.

### False-positive / low-value discovery patterns

- HSBC application URLs (`dco-cc...offerId=...`): application flows, not stable evidence pages.
- HSBC 2018 generic credit-card terms PDF: legal agreement, not a current promotion page.
- HSBC “What is a 0% credit card?”: educational content, not a specific live offer.
- Amex Membership Rewards redemption article: general product education, not a campaign.
- Amex general `Amex Offers` explainer: explains the program but does not expose a stable public list of specific
  personalized offers.
- Crédit Agricole generic card product pages: often describe standing Mastercard cashback/features, not a
  time-bound card promotion.
- Crédit Agricole tariffs page: poor extracted text and no clear campaign evidence in the direct fetch.
- Advanzia `AGB` / generic conditions pages: legal/product terms rather than campaign discovery targets.

## Assessment

The current discovery stage has **high recall but insufficient precision** for automatic acceptance.

It is good at finding the neighborhood around a promotion (product pages, terms, application links, explainers,
comparison pages), but its current score means “possibly promotion-related”, not “safe to add as a campaign source”.
The `score >= 0.5` threshold is especially permissive; several 0.5–0.7 Crédit Agricole results are standing product
features rather than promotions.

Recommended operational policy for now:

1. Keep all discoveries as `pending`; do not auto-accept.
2. Prefer high-scoring pages that contain a concrete reward/rate plus eligibility/date/introductory mechanics.
3. Reject application routes, generic legal terms, educational articles and generic product-feature pages.
4. Add a second-stage content validation step before a lead can be recommended for acceptance.
5. Do not simply raise the global threshold to 0.8: Crédit Agricole's useful package pages score around 0.6–0.8,
   so threshold-only filtering would improve precision at the cost of useful recall.

## Best candidates from this run

Highest-priority pages to review for acceptance:

- Advanzia: `/sonderaktionen` (Playwright)
- Advanzia: `/buyapowa` (Playwright)
- HSBC: Rewards Credit Card product page
- HSBC: Balance Transfer Summary Box PDF
- HSBC: Purchase Plus Summary Box PDF
- Crédit Agricole: `/ouvrir-un-compte.html`
- Amex: official referral page

Amex Gold/Platinum product pages and offer-condition pages are secondary candidates: useful official evidence,
but they do not add independent-domain verification and may increase extraction duplication/noise.

