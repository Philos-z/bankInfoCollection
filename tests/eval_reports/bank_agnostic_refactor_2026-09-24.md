# Bank-agnostic core refactor acceptance — 2026-09-24

## Goal

Keep bank/product-specific knowledge out of production business logic. Concrete bank cases remain useful as
regression fixtures, but adding a new bank should primarily require configuration and source data rather than
new `if bank == ...` or product-name branches.

## Changes

- removed the product-specific card-name alias from `validator/rules.py`;
- generalized comments/prompts in extraction and entity resolution so they refer to product labels,
  colour/edition/tier variants, package tiers and offer mechanics rather than named banks/products;
- strengthened the generic AI arbiter rule:
  - expanded marketing/material wording may still describe the same product;
  - separately marketed colour/edition/tier or personal/business variants remain distinct;
  - the decision must use the full records, not one extra token;
- added `tests/test_bank_agnostic_core.py`, which fails if concrete current bank/product names leak back into
  the generic core files;
- removed the concrete bank example from CLI help.

## Generic matching model

Production matching now relies on generic attributes only:

- bank identity (same bank is a prerequisite, not a bank-specific branch);
- product scope;
- normalized product/card tokens;
- campaign type;
- normalized reward numbers;
- offer mechanics;
- date compatibility;
- title similarity;
- candidate uniqueness;
- conservative AI arbitration for ambiguous product identity.

Card-bundle handling is also generic: one shared umbrella offer can collapse across package tiers when grounded
by the same offer evidence, while multiple distinct components on the same page stay separate.

## Live boundary check

GPT-5.6 Sol was evaluated on two real naming boundaries after the generic prompt change:

- expanded/material product label vs shorter base label -> `same = true`;
- explicit separately named colour/edition variant vs base product -> `same = false`.

No bank/product-specific rule was used for either decision.

## Real rebuild acceptance

The Amex production campaign set was rebuilt from current immutable extractions after removing the product alias.

Result:

- campaigns: **16 active**
- verified: **3**
- conflicts: **0**
- removed: **0**
- linked extractions: **7**
- AI matches: **1**

The previous expanded-label duplicate disappeared through the generic AI arbiter, while the separately named
colour/edition variant remained a distinct verified campaign. Core verified campaigns did not regress.

## Guard / regression result

- case-insensitive production scan outside config/tests/data/docs: **0 concrete bank/product literals**
- no `if bank == ...` / `bank_name == ...` product-specific branches in core logic
- offline suite: **76 tests, all passing**
- SQLite foreign-key check: clean
- SQLite integrity check: `ok`

Concrete bank/product names are intentionally retained in `config/banks.yaml` and regression tests/fixtures,
where they belong.

