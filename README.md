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

## Status: Phases 1–10 Complete + Live-Call Intent Routing

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
│   ├── main.py              # FastAPI app factory (port 8000)
│   ├── compat.py            # Windows fork→spawn multiprocessing patch
│   ├── api/
│   │   ├── dashboard_main.py# Dashboard API factory (port 8001)
│   │   └── routes/
│   │       ├── webhooks.py  # POST /v1/webhooks/calls (Synthflow payload normalizer)
│   │       ├── exceptions.py# Operator exception actions (v1 API)
│   │       ├── dashboard_v2.py # All /dashboard/* endpoints (port 8001)
│   │       └── test_calls.py# POST /v1/test/calls/outbound (dev/staging only)
│   ├── worker/
│   │   ├── main.py          # RQ worker entrypoint
│   │   ├── shadow.py        # log_shadow_action() — single write point for shadow_actions
│   │   └── jobs/
│   │       ├── call_processing.py  # process_call_event, normalize_synthflow_outcome
│   │       ├── ai_jobs.py          # classify_call_event (run_call_analysis) + live-call intent routing
│   │       ├── lifecycle_jobs.py   # update_lead_state (post-AI lead stage update)
│   │       ├── outbound_jobs.py    # launch_outbound_call_job
│   │       ├── channel_jobs.py     # send_sms_job, send_email_job
│   │       ├── voicemail_jobs.py   # process_voicemail_tier (+ executed_actions/duration intent signals)
│   │       └── metrics_jobs.py     # collect_metrics_job (60s self-rescheduling metrics collector)
│   ├── config/
│   │   └── settings.py      # Pydantic settings with mode flags
│   ├── adapters/            # External service clients
│   │   ├── ghl.py
│   │   ├── synthflow.py     # schedule_callback + launch_new_lead_call
│   │   ├── openai_client.py
│   │   └── sheets.py
│   ├── models/              # SQLAlchemy ORM (13 tables)
│   └── services/            # Business logic
│       ├── dashboard_metrics.py # get_metrics(), get_health() — pure SQL aggregates
│       ├── alerting.py          # evaluate_alerts(), SMTP send, 5 alert types
│       ├── pipeline_trace.py    # get_lead_trace() — per-lead timeline
│       └── event_publisher.py  # publish_event() — non-fatal DB event writes
├── dashboard-ui/            # Next.js 14 frontend (port 3000)
│   ├── app/                 # App Router pages
│   ├── components/          # React components
│   ├── lib/api.ts           # Typed API client (all fetch calls)
│   └── types/index.ts       # TypeScript response types
├── execution/
│   ├── dashboard.py          # Streamlit monitoring dashboard (read-only, legacy)
│   └── test_scripts/
│       ├── run_test_call.py  # CLI: trigger a live end-to-end test call
│       └── watch_test_call.py# CLI: poll DB for test call result
├── migrations/
│   └── versions/
│       ├── 0001_initial_schema.py               # 8 core tables
│       ├── 0002_reporting_views.py              # fact_call_activity, fact_kpi_daily
│       ├── 0003_audit_log.py                    # audit_log table
│       ├── 0004_call_event_synthflow_fields.py  # model_id, timeline, telephony_*
│       ├── 0005_lead_state_intent_fields.py     # status, do_not_call, invalid, next_action_at
│       ├── 0006_messaging_tables.py             # outbound_messages, inbound_messages
│       ├── 0007_shadow_actions.py               # shadow_actions table
│       ├── 0008_call_event_detected_intent.py   # detected_intent field on call_events
│       ├── 0009_app_config.py                   # app_config table (runtime settings)
│       ├── 0010_brand_config.py                 # brand/messaging config fields
│       ├── 0011_dashboard_tables.py             # system_metrics, event_stream, alert_events
│       └── 0012_call_event_voice_agent.py       # voice_agent field on call_events
├── tests/
│   ├── unit/
│   └── integration/
├── directives/
│   ├── spec/                # Binding specifications (00–17 + dashboard/)
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
- [0001_initial_schema.py](migrations/versions/0001_initial_schema.py) — 8 core tables with unique constraints and indexes
- [0002_reporting_views.py](migrations/versions/0002_reporting_views.py) — `fact_call_activity` and `fact_kpi_daily` views
- [0003_audit_log.py](migrations/versions/0003_audit_log.py) — `audit_log` table for operator action trail
- [0004_call_event_synthflow_fields.py](migrations/versions/0004_call_event_synthflow_fields.py) — `model_id`, `timeline`, telephony fields on `call_events`
- [0005_lead_state_intent_fields.py](migrations/versions/0005_lead_state_intent_fields.py) — intent fields on `lead_state`
- [0006_messaging_tables.py](migrations/versions/0006_messaging_tables.py) — `outbound_messages` and related tables
- [0007_shadow_actions.py](migrations/versions/0007_shadow_actions.py) — `shadow_actions` table for shadow mode interception log
- [0008_call_event_detected_intent.py](migrations/versions/0008_call_event_detected_intent.py) — `detected_intent` column on `call_events`
- [0009_app_config.py](migrations/versions/0009_app_config.py) — `app_config` table for runtime settings
- [0010_brand_config.py](migrations/versions/0010_brand_config.py) — brand and messaging configuration fields
- [0011_dashboard_tables.py](migrations/versions/0011_dashboard_tables.py) — `system_metrics`, `event_stream`, `alert_events` tables (required for Dashboard v2)
- [0012_call_event_voice_agent.py](migrations/versions/0012_call_event_voice_agent.py) — `voice_agent` column on `call_events` (used for campaign resolution fallback)

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

## Dashboard v2

The v2 dashboard is a production-grade monitoring and analytics console. It replaces the legacy Streamlit dashboard for all real-time operational use.

- **Backend API** — FastAPI on port 8001 (`app/api/dashboard_main.py`), separate from the main pipeline API on port 8000
- **Frontend** — Next.js 14 app on port 3000 (`dashboard-ui/`)
- **Event delivery** — Redis Pub/Sub channel `dashboard:events` bridged to a WebSocket; cursor-based polling fallback via `GET /dashboard/events`
- **Auth** — read endpoints optionally gated by `DASHBOARD_READ_AUTH_REQUIRED`; all write/action endpoints require `Authorization: Bearer {SECRET_KEY}`

### Starting the dashboard

```bash
# 1. Apply migration (one-time, requires Postgres running)
alembic upgrade head

# 2. Start the dashboard API on port 8001
uvicorn app.api.dashboard_main:app --port 8001 --reload

# 3. Install frontend deps (one-time)
cd dashboard-ui
npm install

# 4. Start the Next.js frontend on port 3000
npm run dev
```

Open http://localhost:3000 in your browser.

### Dashboard pages

| Route | Purpose |
|---|---|
| `/` | Home — status strip, live alert rows, navigation cards with live badge counts |
| `/activity` | Real-time event stream (WebSocket) of all worker job events |
| `/campaign-overview` | Upcoming scheduled contacts — date-window filter, campaign filter, drill-down to contact detail |
| `/contact-lookup` | Search any contact by phone or ID to view full detail and pipeline state |
| `/exceptions` | Exceptions Monitor — open issue queue with Resolve / Ignore / Bulk-Ignore actions, trend chart, date/type/severity filters |
| `/system-anomalies` | Spike detection, recurring issues table, failure clusters, 14-day frequency trend |
| `/queue` | Stuck jobs and expired worker leases |
| `/alerts` | Threshold alerts (queue lag, error rate, exception spike, worker offline, GHL auth failure) |
| `/voice-performance` | Single-screen voice analytics: KPI sidebar, stacked trends chart, WoW waterfall, efficiency scatter |
| `/ai-performance` | AI quality metrics, intent distribution, consent distribution, intent→outcome table, error trends |
| `/engagement-analysis` | Cross-filter analytics: KPIs by campaign, direction, voice agent, and date range; consent distribution filtered by all active filters |
| `/conversion-funnel` | Funnel visual (Total Calls → Picked Up → Engaged → Booked), step table, drop-off highlight, trend lines |
| `/crm-health` | GHL task and VM update success rates, shadow write count |
| `/lead/[id]` | Per-contact pipeline trace — full job history, shadow flags, failure reasons |
| `/settings` | Runtime settings management — brand config, messaging config, thresholds |

### Dashboard API endpoints (port 8001)

| Method | Path | Description |
|---|---|---|
| `GET` | `/dashboard/health` | System health snapshot (queue lag, workers, exceptions, mode flags) |
| `GET` | `/dashboard/metrics` | Aggregate KPIs, AI distribution, queue state, CRM rates — filterable by `campaign`, `direction`, `voice_agent`, `from_date`, `to_date` |
| `GET` | `/dashboard/card-metrics` | Compact KPI card set for the home page navigation cards |
| `GET` | `/dashboard/events` | Cursor-based event stream (polling fallback) |
| `WS` | `/dashboard/ws/events` | WebSocket real-time event stream |
| `GET` | `/dashboard/lead/{id}/trace` | Per-contact job timeline |
| `GET` | `/dashboard/lead/{id}/detail` | Full contact detail (lead state, call history, scheduled jobs) |
| `GET` | `/dashboard/alerts` | Alert records (filter by status) |
| `GET` | `/dashboard/exceptions` | Exception records (filter by status, severity, type, date) |
| `GET` | `/dashboard/exceptions/trend` | Daily exception counts by type (for trend chart) |
| `GET` | `/dashboard/exceptions/anomalies` | Spike detection, recurring issues, failure clusters |
| `GET` | `/dashboard/voice-performance` | Voice KPIs, WoW changes, weekly time series, campaign breakdown |
| `GET` | `/dashboard/ai-timeseries` | Weekly AI quality trend (blank transcript rate, unknown intent %) |
| `GET` | `/dashboard/campaign-overview` | Leads with upcoming scheduled actions — filterable by date window |
| `GET` | `/dashboard/recent-calls` | Recent call event log — filterable by date, voice agent, limit |
| `GET` | `/dashboard/intent-calls` | Call records for a specific intent — filterable by campaign, direction, voice agent |
| `GET` | `/dashboard/settings` | Current runtime app config values |
| `POST` | `/dashboard/settings` | Save updated runtime app config values (auth required) |
| `POST` | `/dashboard/actions/retry` | Re-enqueue a failed job |
| `POST` | `/dashboard/actions/cancel` | Cancel all pending jobs for a contact |
| `POST` | `/dashboard/actions/finalize` | Force-advance a contact to terminal state |
| `POST` | `/dashboard/actions/resolve` | Resolve a specific exception |
| `POST` | `/dashboard/actions/ignore` | Ignore a specific exception |
| `POST` | `/dashboard/actions/bulk-ignore` | Ignore all open exceptions of a given type |

### Environment variables for dashboard

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8001` | Dashboard API base URL (set in `dashboard-ui/.env.local`) |
| `NEXT_PUBLIC_DASHBOARD_TOKEN` | — | Bearer token for write actions (dev only; prod uses `localStorage`) |
| `DASHBOARD_READ_AUTH_REQUIRED` | `false` | Require auth token on read endpoints |
| `ALLOW_ORIGINS` | `http://localhost:3000` | CORS origin for the Next.js frontend |
| `ALERT_QUEUE_LAG_THRESHOLD_SECONDS` | `300` | Queue lag threshold for `queue_lag_exceeded` alert |
| `ALERT_ERROR_RATE_THRESHOLD` | `0.2` | Error rate threshold for `error_rate_spike` alert |
| `ALERT_EXCEPTION_COUNT_THRESHOLD` | `10` | Open exception count threshold for `exception_spike` alert |

### Dashboard background workers

The metrics collector (`collect_metrics_job`) runs every 60 seconds as a self-rescheduling RQ job. It is started automatically by the worker on startup via `start_metrics_scheduler()`. Each run inserts one row into `system_metrics` and evaluates all alert thresholds.

### Feature documentation

See [`docs/dashboard-feature-usecases.md`](docs/dashboard-feature-usecases.md) for a detailed description of every page, every section within each page, and the specific business question each feature answers.