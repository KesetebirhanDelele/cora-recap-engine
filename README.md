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
│   ├── compat.py            # Windows fork→spawn multiprocessing patch
│   ├── api/
│   │   └── routes/
│   │       ├── webhooks.py  # POST /v1/webhooks/calls (Synthflow payload normalizer)
│   │       ├── exceptions.py# Operator dashboard actions
│   │       └── test_calls.py# POST /v1/test/calls/outbound (dev/staging only)
│   ├── worker/
│   │   ├── main.py          # RQ worker entrypoint
│   │   ├── shadow.py        # log_shadow_action() — single write point for shadow_actions
│   │   └── jobs/
│   │       ├── call_processing.py  # process_call_event, normalize_synthflow_outcome
│   │       ├── ai_jobs.py          # classify_call_event (run_call_analysis)
│   │       ├── outbound_jobs.py    # launch_outbound_call_job
│   │       ├── channel_jobs.py     # send_sms_job, send_email_job
│   │       └── voicemail_jobs.py   # process_voicemail_tier
│   ├── config/
│   │   └── settings.py      # Pydantic settings with mode flags
│   ├── adapters/            # External service clients
│   │   ├── ghl.py
│   │   ├── synthflow.py     # schedule_callback + launch_new_lead_call
│   │   ├── openai_client.py
│   │   └── sheets.py
│   ├── models/              # SQLAlchemy ORM
│   └── services/            # Business logic
├── execution/
│   ├── dashboard.py          # Streamlit monitoring dashboard (read-only)
│   └── test_scripts/
│       ├── run_test_call.py  # CLI: trigger a live end-to-end test call
│       └── watch_test_call.py# CLI: poll DB for test call result
├── migrations/
│   └── versions/
│       ├── 0001_initial_schema.py       # 8 tables
│       ├── 0002_reporting_views.py      # fact_call_activity, fact_kpi_daily
│       ├── 0003_audit_log.py            # audit_log table
│       └── 0004_call_event_synthflow_fields.py  # model_id, timeline, telephony_*
├── tests/
│   ├── unit/
│   └── integration/
├── directives/
│   ├── spec/                # Binding specifications
│   └── adr/                 # Architecture decision records
├── Dockerfile               # python:3.12-slim image
├── docker-compose.yml       # Redis + API + Worker (single command startup)
├── .dockerignore
├── .env.example             # Environment variable template
└── pyproject.toml           # Dependencies and tooling config
```

---

## Local Development (Recommended)

Start the entire stack — Redis, API, and Worker — with one command:

```powershell
# first time
docker compose up --build
# subsequent runs   
docker compose up
# watch worker process jobs           
docker compose logs -f worker  
```

| Service | URL |
|---|---|
| FastAPI API | http://localhost:8000 |
| API docs (requires `APP_DEBUG=true`) | http://localhost:8000/docs |
| Redis | localhost:6379 |

Docker Compose handles the startup order automatically. Redis is always ready before the API or worker starts.

**First-time setup:**
```powershell
# Copy environment template and fill in credentials
cp .env.example .env
# then edit .env

# Build and start
docker compose up --build
```

**Rebuild after dependency changes** (e.g. new packages in `pyproject.toml`):
```powershell
docker compose up --build
```

**Run in background:**
```powershell
docker compose up -d
docker compose logs -f   # tail all logs
docker compose logs -f worker  # tail worker only
```

**Stop everything:**
```powershell
docker compose down
```

---

## Local Setup (Manual / Without Docker)

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
- [0001_initial_schema.py](migrations/versions/0001_initial_schema.py) — 8 tables with unique constraints and indexes
- [0002_reporting_views.py](migrations/versions/0002_reporting_views.py) — `fact_call_activity` and `fact_kpi_daily` views
- [0003_audit_log.py](migrations/versions/0003_audit_log.py) — `audit_log` table for operator action trail
- [0004_call_event_synthflow_fields.py](migrations/versions/0004_call_event_synthflow_fields.py) — `model_id`, `timeline`, telephony fields on `call_events`
- [0005_lead_state_intent_fields.py](migrations/versions/0005_lead_state_intent_fields.py) — intent fields on `lead_state`
- [0006_messaging_tables.py](migrations/versions/0006_messaging_tables.py) — `outbound_messages` and related tables
- [0007_shadow_actions.py](migrations/versions/0007_shadow_actions.py) — `shadow_actions` table for shadow mode interception log

### Start services

```bash
# Start API
cora-api
# or: uvicorn app.main:app --reload

# Start worker (requires Redis)
cora-worker
# or: python -m app.worker.main
```

### Connect to Postgres

```powershell
psql -h localhost -p 5433 -U postgres -d cora
```

Shortcut (run once, then use `cora-db`):
```powershell
Set-Alias cora-db "psql -h localhost -p 5433 -U postgres -d cora"
```

---

## Development Startup

Three services must run simultaneously. **Start them in this order — Redis first.**

```
Redis  →  RQ Worker  →  FastAPI API
```

### Terminal 1 — Redis (Docker)

```powershell
docker run -p 6379:6379 redis
```

Wait for:
```
Ready to accept connections
```

Verify Redis is reachable:
```powershell
python -c "import redis; r=redis.Redis(host='localhost', port=6379); print(r.ping())"
```
Expected: `True`

### Terminal 2 — FastAPI server

```powershell
uvicorn app.main:app --reload
```

Expected:
```
Uvicorn running on http://127.0.0.1:8000
Redis connected | queue=default
```

### Terminal 3 — RQ worker

```powershell
python -m app.worker.main
```

Expected:
```
Listening on default, ai, callbacks, retries, sheet_mirror...
```

---

## Monitoring Dashboard

A read-only Streamlit dashboard at `execution/dashboard.py` queries Postgres directly and displays live system state. The API and worker do **not** need to be running — only Postgres.

### Install and run

```powershell
# One-time install (inside the project venv)
.venv\Scripts\activate
pip install streamlit

# Launch
streamlit run execution/dashboard.py
```

Opens at `http://localhost:8501` automatically.

> **Note:** Alembic (and the dashboard) read `DATABASE_URL` from `.env`. If you see a connection error pointing to `host.docker.internal`, a shell environment variable is overriding `.env`. Clear it with:
> ```powershell
> Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
> ```

### Dashboard sections

| Section | What it shows |
|---|---|
| **Overview** | Calls (24 h), shadow actions total, open exceptions, failed jobs (24 h); bar charts for job status and shadow action types |
| **Trends** | Date-range trend charts per campaign (New Lead, Cold Lead, Inbound): total calls, errors, % completed call, % Goodbye — by day/week/month |
| **Recent Calls** | Last N call events joined to lead state — contact, status, duration, transcript preview, campaign |
| **Lead State** | All leads; filter by status (`active`, `nurture`, `enrolled`, `closed`) and campaign |
| **Shadow Actions** | Intercepted outbound actions logged when `SHADOW_MODE_ENABLED=true`; filter by type (`outbound_call`, `sms`, `email`) |
| **Scheduled Jobs** | Job queue; filter by status and job type |
| **Exceptions** | Open/resolved/ignored exceptions; filter by severity |
| **Contact Drill-Down** | Enter a `contact_id` to see all data for that contact across every table |

### Shadow mode and the dashboard

When `SHADOW_MODE_ENABLED=true`, all outbound actions (calls, SMS, email) are intercepted before reaching Synthflow or the AI layer. Each interception writes one row to `shadow_actions`. The **Shadow Actions** and **Overview** sections of the dashboard show these rows so you can verify what _would have_ been sent in production.

To switch to live mode, set `SHADOW_MODE_ENABLED=false` in `.env` (requires explicit approval per the autonomous execution contract).

---

## End-to-End Test Call

Trigger a real outbound Synthflow call to verify the full pipeline:

**Option A — via the test script (recommended)**
```powershell
python execution/test_scripts/run_test_call.py --phone +15714782790 --lead-name "Test User" --campaign-name New_Lead
```

**Option B — hit Synthflow's Make Call workflow directly (bypasses the app)**
```powershell
$body = @{
    phone_number = "+15714782790"
    lead_name    = "Test User"
    campaign_name = "New_Lead"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri $env:SYNTHFLOW_LAUNCH_WORKFLOW_URL `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

After the call is placed the worker logs should show:
```
Processing job <uuid>
launch_outbound_call_job: Synthflow call launched
```

After the call ends, Synthflow posts the result to `POST /v1/webhooks/calls`. Watch for:
```
process_call_event: stored CallEvent | call_id=<id>
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Redis unavailable at startup — webhook jobs will be stored in Postgres only` | Redis not running when API started | Start Redis first (Terminal 1), then restart the API |
| Worker shows only registry cleanup, no jobs processed | Job stored in Postgres but not enqueued to Redis | Restart the API after Redis is confirmed running |
| `cannot find context for 'fork'` | RQ imported before compat patch applied | Restart the API — this is fixed in `app/compat.py` |
| `422 Unprocessable Entity` on webhook | Synthflow payload field names don't match schema | Webhook normalizer in `webhooks.py` handles this — check logs for raw payload |

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
| `SHADOW_MODE_ENABLED` | `true` | Intercepts all outbound actions (calls, SMS, email); logs to `shadow_actions` instead of executing |
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

## Code to see Redus Queue:
docker run -p 9181:9181 `
--network cora-recap-engine_default `
-e RQ_DASHBOARD_REDIS_URL=redis://redis:6379 `
eoranged/rq-dashboard


## Flushing old Redis Queues
docker exec -it cora-recap-engine-redis-1 redis-cli
127.0.0.1:6379> flushall
OK
127.0.0.1:6379> 
