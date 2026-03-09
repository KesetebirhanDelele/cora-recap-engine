# Cora Recap Engine

Python-based API + worker platform that replaces Zapier workflows for inbound recap, outbound cold-lead recap, and outbound new-lead recap.

Provides durable Postgres-backed state, Redis/RQ job execution, GHL CRM updates, Synthflow callback scheduling, OpenAI-generated summaries, and consent-gated recap writeback.

---

## Architecture

```
┌─────────────────────┐    ┌────────────────────────┐
│   API Service       │    │   Worker Service       │
│   (FastAPI)         │    │   (RQ)                 │
│                     │    │                        │
│  POST /v1/webhooks  │    │  Queues:               │
│  GET  /dashboard    │    │    default             │
│  POST /exceptions   │    │    ai                  │
└────────┬────────────┘    │    callbacks           │
         │                 │    retries             │
         ▼                 │    sheet_mirror        │
┌─────────────────────┐    └────────────┬───────────┘
│   Postgres          │◄───────────────┘
│   (authoritative    │
│    state store)     │
└─────────────────────┘
         │
         ├── GHL / LeadConnector (CRM authority)
         ├── Redis + RQ (queue execution only)
         ├── OpenAI (AI analysis, summaries, consent)
         ├── Synthflow (callback scheduling)
         └── Google Sheets (mirror-only, shadow mode)
```

**Layer boundaries:**
- **Postgres** — authoritative for campaign state, call events, audit, exceptions, scheduled jobs
- **Redis/RQ** — job execution only; Postgres is canonical (jobs survive worker restart)
- **GHL** — CRM authority for contacts, tasks, notes, and custom fields
- **Google Sheets** — shadow mirror only; production routing never reads from Sheets

---

## Status: Phases 1–10 Complete

All build phases are complete. Phase 9 (Google Sheets shadow sync) is **out of scope** — visual inspection is used instead.

| Phase | Status |
|---|---|
| 1–8 | Complete |
| 9 (Sheets sync) | Out of scope |
| 10 (Integration + evals) | Complete |

---

## Repository Structure

```
cora-recap-engine/
├── app/
│   ├── main.py              # FastAPI app factory
│   ├── api/
│   │   └── routes/
│   │       ├── webhooks.py  # POST /v1/webhooks/calls
│   │       └── exceptions.py# Operator dashboard actions
│   ├── worker/
│   │   └── main.py          # RQ worker entrypoint
│   ├── config/
│   │   └── settings.py      # Pydantic settings with mode flags
│   ├── adapters/            # External service clients (stubs until Phase 4+)
│   │   ├── ghl.py
│   │   ├── synthflow.py
│   │   ├── openai_client.py
│   │   └── sheets.py
│   ├── models/              # SQLAlchemy ORM (Phase 3)
│   └── services/            # Business logic (Phase 2+)
├── migrations/              # SQL migration files (Phase 3)
├── tests/
│   ├── unit/
│   └── integration/
├── directives/
│   ├── spec/                # Binding specifications
│   └── adr/                 # Architecture decision records
├── tmp/                     # Scratch space — never committed
├── .env.example             # Environment variable template
└── pyproject.toml           # Dependencies and tooling config
```

---

## Local Setup

### Prerequisites
- Python 3.11+
- Postgres (see `.env.example` for connection config)
- Redis (see `.env.example` for connection config)

### Install

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install app + dev dependencies
pip install -e ".[dev]"
```

### Environment

```bash
cp .env.example .env
# Edit .env — fill in credentials for your environment
```

> **Shadow mode is on by default.** `GHL_WRITE_MODE=shadow` and `GHL_WRITE_SHADOW_LOG_ONLY=true` are the safe defaults. Do not change to `live` without explicit approval.

### Dashboard API

All dashboard routes require: `Authorization: Bearer {SECRET_KEY}`

| Method | Path | Description |
|---|---|---|
| `GET` | `/v1/exceptions` | List exceptions (filter by `status`, `severity`, `search`) |
| `GET` | `/v1/exceptions/{id}` | Exception detail + audit trail |
| `POST` | `/v1/exceptions/{id}/retry-now` | Re-enqueue job immediately |
| `POST` | `/v1/exceptions/{id}/retry-delay` | Re-enqueue with delay (`{"delay_minutes": 60}`) |
| `POST` | `/v1/exceptions/{id}/cancel-future-jobs` | Cancel all pending jobs for entity |
| `POST` | `/v1/exceptions/{id}/force-finalize` | Advance to terminal, resolve exception |

Optional header: `X-Operator-Id: your-name` (defaults to `dashboard`)

Conflict responses: HTTP 409 when two operators act simultaneously — first wins.

### Database migrations

Migrations use [Alembic](https://alembic.sqlalchemy.org/). `DATABASE_URL` in `.env` must point to a running Postgres instance.

```bash
# Apply all migrations (creates all tables and reporting views)
alembic upgrade head

# Check current revision
alembic current

# Roll back one step
alembic downgrade -1

# Generate offline SQL (for DBA review before applying)
alembic upgrade head --sql > migrations/upgrade.sql
```

Migration files:
- [migrations/versions/0001_initial_schema.py](migrations/versions/0001_initial_schema.py) — 8 tables with unique constraints and indexes
- [migrations/versions/0002_reporting_views.py](migrations/versions/0002_reporting_views.py) — `fact_call_activity` and `fact_kpi_daily` views

### Start services

```bash
# Start API
cora-api
# or: uvicorn app.main:app --reload

# Start worker (requires Redis)
cora-worker
# or: python -m app.worker.main
```

### Health check

```bash
curl http://localhost:8000/health
```

---

## Testing

```bash
# Run all unit tests (no credentials required)
pytest tests/unit/

# Run with coverage
pytest tests/unit/ --cov=app --cov-report=term-missing

# Lint
ruff check .

# Type check
mypy app/
```

Integration tests require real or sandboxed service credentials and must be opted in:

```bash
INTEGRATION_TESTS=1 pytest tests/integration/
```

---

## Mode Flags

| Variable | Default | Meaning |
|---|---|---|
| `GHL_WRITE_MODE` | `shadow` | GHL writes are logged but not executed |
| `GHL_WRITE_SHADOW_LOG_ONLY` | `true` | Shadow payloads are log-only |
| `SHADOW_MODE_ENABLED` | `true` | Global shadow flag |
| `GOOGLE_SHADOW_MODE_ENABLED` | `true` | Sheets in mirror-only mode |

To enable real GHL writes: set `GHL_WRITE_MODE=live` and `GHL_WRITE_SHADOW_LOG_ONLY=false`. This requires explicit approval per the autonomous execution contract.

---

## Unresolved External IDs

The following must be supplied before the corresponding write paths go live:

- `GHL_FIELD_VM_EMAIL_HTML`, `GHL_FIELD_VM_EMAIL_SUBJECT`, `GHL_FIELD_VM_SMS_TEXT`
- `GHL_FIELD_LAST_CALL_STATUS`, `GHL_FIELD_MARK_AS_LEAD`, `GHL_FIELD_NOTES`
- `GHL_TASK_PIPELINE_ID`, `GHL_TASK_DEFAULT_OWNER_ID`
- `GOOGLE_SHEETS_CALL_LOG_ID`, `GOOGLE_SHEETS_CAMPAIGN_DATA_ID`, and all tab names
- New Lead VM tier delays: `NEW_VM_TIER_*`

See `.env.example` for the full list.

---

## Runbook

See [directives/spec/11_runbook.md](directives/spec/11_runbook.md).

Full specifications: [directives/spec/](directives/spec/)
Architecture decisions: [directives/adr/](directives/adr/)
