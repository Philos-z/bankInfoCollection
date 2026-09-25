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
python main.py discover
python main.py validate-leads
python main.py leads
```

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

