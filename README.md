# bankInfoCollection

European bank card/campaign collection pipeline with grounded AI extraction, multi-source verification,
source discovery, source-health monitoring, and lifecycle tracking.

## V1 capabilities

- Declarative bank/source configuration in `config/banks.yaml`
- `requests`, Playwright and PDF crawling
- Content-hash snapshots with CSS-selector support
- GPT-based multilingual campaign extraction into a shared schema
- Evidence-quote grounding and normalization
- Bank-agnostic entity resolution
- Multi-domain verification / conflict detection
- Campaign lifecycle tracking
- Two-stage source discovery with a human acceptance gate
- Source health and failure streaks
- Incremental/idempotent runs

Current V1 acceptance status is documented in:

`tests/eval_reports/v1_system_acceptance_2026-09-25.md`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in your own AI proxy/API settings in `.env`.

Initialize the database and sync configured sources:

```bash
python main.py init
```

Run the normal incremental pipeline:

```bash
python main.py run
```

Useful commands:

```bash
python main.py health
python main.py list
python main.py report --verified-only
python main.py report --campaign 88
python main.py report --campaign 88 --json
python main.py report --system
python main.py discover
python main.py validate-leads
python main.py leads
```

### Campaign reporting

`report` is the user-facing result layer. It reads canonical campaigns plus all linked source evidence and does
not need to recrawl pages.

```bash
# current user-facing campaigns
python main.py report

# only cross-source verified campaigns
python main.py report --verified-only

# filters
python main.py report --bank bbva_es
python main.py report --type welcome_bonus
python main.py report --expiring

# one campaign with source/evidence/original-page provenance
python main.py report --campaign 88

# machine-readable output, including retained evidence/snapshot history
python main.py report --campaign 88 --json
```

Each detailed source entry includes the live URL, evidence quote, confidence, fetch time, snapshot ID, raw
HTML/PDF snapshot path, normalized-text snapshot path and all retained evidence versions for that source.

### Action-oriented fact enrichment

Reporting can persist a GPT-assisted fact layer so users can immediately see **who is eligible, what to do,
which condition earns which reward, deadlines, exclusions and warnings**. GPT is used only to structure facts
already present in canonical campaigns/extractions; every persisted fact must cite one or more extraction IDs.

```bash
# enrich one campaign
python main.py enrich --campaign 88

# enrich only cross-source verified campaigns
python main.py enrich --verified-only

# rerun only when you explicitly want to regenerate current facts
python main.py enrich --campaign 88 --redo
```

The persisted layer uses:

- `campaign_enrichments`: model/input hash/cache status
- `campaign_facts`: headline, eligibility, required steps, action→reward facts, deadlines, warnings, etc.
- `fact_evidence`: fact → extraction/snapshot provenance

If campaign or extraction input changes, the stored input hash no longer matches and Report marks the enrichment
`stale` instead of showing the old action plan. Numeric amounts, thresholds and dates produced by GPT are checked
against the cited extraction evidence before persistence.

## Data and secrets

Runtime data is intentionally not committed:

- `.env`
- `data/` (SQLite DB, snapshots, backups)
- `.venv/`

Use `.env.example` as a template only.

## Tests

```bash
python -m unittest discover -s tests -v
```

The V1 baseline currently passes the full offline regression suite.

