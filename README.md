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
         ▼                 └────────────┬───────────┘
┌─────────────────────┐                │
│   PgBouncer         │◄───────────────┘
│   (connection pool) │
│   transaction mode  │
│   20 server conns   │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Postgres          │
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
│       ├── run_test_call.py       # CLI: trigger a live end-to-end test call
│       ├── watch_test_call.py     # CLI: poll DB for test call result
│       └── test_ghl_writes.py     # Pre-go-live GHL write integration test
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
# Create and fill in .env with your credentials
# (see Mode Flags section and directives/spec/11_runbook.md for required variables)

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

Create `.env` in the project root and fill in credentials for your environment.

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
| `GHL_WRITE_CONTACT_FIELDS` | `false` | Enable field update writes |
| `GHL_WRITE_TASKS` | `false` | Enable task creation writes |
| `GHL_WRITE_SUMMARY` | `false` | Enable student summary delivery |
| `GHL_WRITE_CAMPAIGN_STATE` | `false` | Enable campaign state field writes |
| `GHL_WRITE_FINALIZATION` | `false` | Enable voicemail finalization writes |
| `SHADOW_MODE_ENABLED` | `true` | Intercepts all outbound actions (calls, SMS, email); logs to `shadow_actions` instead of executing |

All mode flags are DB-backed. Changes made on the **System Controls** dashboard page (`/system-controls`) take effect immediately on the next job — no `.env` edit or restart required.

To go live: use the System Controls dashboard to set `GHL_WRITE_MODE=live`, `SHADOW_MODE_ENABLED=false`, and enable each write category. See `directives/spec/dashboard/11_runbook.md` for the full go-live procedure.

---

## GHL Private Integration Token

`GHL_API_KEY` must be a **GHL Private Integration JWT token** — not a simple API key.

Create one at: GHL → Settings → Integrations → Private Integrations.

Required scopes:
- `contacts.readonly` — search and fetch contacts
- `contacts.write` — update custom fields, create tasks, append notes
- `locations/tasks.write` — task creation
- `locations/customFields.readonly` — field label→UUID resolution (required for all field writes)

Tokens can expire or be revoked. If you receive 401 errors, regenerate the token, update `.env`, and restart the API and worker services.

Run `execution/test_scripts/test_ghl_writes.py` to verify all GHL writes before going live:

```bash
python execution/test_scripts/test_ghl_writes.py --phone +1XXXXXXXXXX
```

---

## Unresolved External IDs

The following are not yet configured and block those specific write paths:

- `GHL_FIELD_LAST_CALL_STATUS`, `GHL_FIELD_NOTES` — planned, not yet live
- `GHL_TASK_PIPELINE_ID`, `GHL_TASK_DEFAULT_OWNER_ID` — planned, not yet live

Note: `GHL_FIELD_MARK_AS_LEAD` — the `Mark as Lead` custom field does not currently exist in GHL. The write path is implemented but silently skips until the field is created in GHL and the label configured in `.env`.

See `directives/spec/11_runbook.md` for the go-live procedure.

---

## Runbook

See [directives/spec/11_runbook.md](directives/spec/11_runbook.md).

Full specifications: [directives/spec/](directives/spec/)
Architecture decisions: [directives/adr/](directives/adr/)

---

## Production Deployment (Hetzner Cloud)

### Server requirements
- Hetzner CX22 (2 vCPU, 4 GB RAM) or larger
- Ubuntu 22.04
- Docker CE + Docker Compose plugin installed

### One-time server setup

```bash
# Install Docker CE on the Hetzner Ubuntu VM
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Clone the repo (use a GitHub PAT for private repos)
git clone https://github.com/<org>/cora-recap-engine /opt/cora-recap-engine
cd /opt/cora-recap-engine
```

### .env on the server

Copy your working local `.env` to the server, then adjust these values:

```
APP_ENV=production
REDIS_HOST=redis
DASHBOARD_API_URL=http://<server-ip>:8001
WS_URL=ws://<server-ip>:8001/ws
ALLOW_ORIGINS=http://<server-ip>:3000
```

**Critical rules:**
- App services connect through PgBouncer — `DATABASE_URL` is set to `pgbouncer:5432` directly in `docker-compose.yml` and does not need to be in `.env`. Only `POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, and `POSTGRES_DATABASE` are needed in `.env`.
- `migrate` and `adminer` connect directly to `postgres:5432` (bypassing PgBouncer) — this is hardcoded in `docker-compose.yml` and requires no `.env` entry.
- `DASHBOARD_API_URL` must be the **public server IP** (not localhost) — it is baked into the Next.js bundle at build time and used by the browser
- `ALLOW_ORIGINS` must match the origin the browser uses to open the dashboard
- Do NOT copy `docker-compose.override.yml` to the server — it is local dev only

### Deploy

```bash
cd /opt/cora-recap-engine
docker compose up -d --build
docker compose logs migrate       # verify migrations ran (should exit 0)
docker compose logs pgbouncer     # verify "listening on 0.0.0.0:5432"
docker compose ps                 # all services should be healthy/running
```

### Open firewall ports (Hetzner Cloud Console)

In Hetzner Cloud Console → your server → Firewalls, add inbound TCP rules for:
- Port `8000` — Synthflow webhook intake
- Port `8001` — Dashboard API (browser)
- Port `3000` — Next.js frontend

### Point Synthflow webhook

In Synthflow → your workflow → HTTP step, set:
```
Method: POST
URL: http://<server-ip>:8000/v1/webhooks/calls
```

Use `http://` not `https://` — TLS is not configured without a reverse proxy.

### Verify end-to-end

```bash
# API healthy
curl http://<server-ip>:8000/health
# → {"status":"ok","service":"cora-recap-engine"}

# Dashboard API healthy
curl http://<server-ip>:8001/health
# → {"status":"ok","service":"dashboard-api"}

# After a Synthflow call completes
docker compose logs api --tail=20
# → INFO: Received call event | call_id=... job_id=... enqueued=True

docker compose logs worker-default --tail=30
docker compose logs worker-ai --tail=30
```

### Production troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `migrate` fails with `host.docker.internal` error | `docker-compose.override.yml` present on server, or `.env` has `DATABASE_URL=...localhost...` | Remove override file; set `DATABASE_URL` to use `postgres:5432` in `.env` |
| `migrate` fails even after fix | Old image cached with wrong DATABASE_URL | `docker compose build --no-cache migrate && docker compose up migrate` |
| frontend shows `Couldn't find pages or app directory` | Dev Dockerfile used instead of prod | `docker compose build --no-cache frontend && docker compose up -d frontend` |
| `TypeError: Failed to fetch` on dashboard pages | `DASHBOARD_API_URL` was `localhost` when the image was built — baked wrong URL | Set correct server IP in `.env`, then `docker compose up -d --build frontend` |
| `TypeError: Failed to fetch` persists after rebuild | Browser cached old JS bundle | Hard reload: `Ctrl+Shift+R` |
| `TypeError: Failed to fetch` persists after hard reload | Port 8001 blocked by Hetzner firewall | Add inbound TCP 8001 rule in Hetzner Cloud Console → Firewalls |
| Synthflow POST returns connection error | URL uses `https://` — server has no TLS cert | Change Synthflow HTTP step URL to `http://` |
| Synthflow POST returns 422 `missing_call_id` | Payload wrapped under `{"data": {...}}` and wasn't unwrapped | Already fixed in normalizer — pull latest and rebuild `api` |
| Dashboard shows CORS error in browser | `ALLOW_ORIGINS` set to `localhost:3000` but browser hits `<server-ip>:3000` | Set `ALLOW_ORIGINS=http://<server-ip>:3000` in `.env`, restart `dashboard-api` |
| All data shows zeros / null | No call events in DB yet | Normal on fresh deploy — send real calls via Synthflow first |
| Worker not processing jobs | Redis not healthy | `docker compose logs redis` — restart if needed; worker reconnects automatically |

### Adminer — browser-based DB UI (port 8080)

Adminer is included in `docker-compose.yml` as an optional service that provides a full browser-based SQL interface (table browser + query runner + CSV export). It connects to the Postgres container over Docker's internal network.

It is bound to `127.0.0.1:8080` only (not publicly exposed). Access it via an SSH tunnel:

```bash
# From your local machine:
ssh -L 8080:127.0.0.1:8080 root@<server-ip>
# Then open: http://localhost:8080
```

Login credentials:
- **System**: PostgreSQL
- **Server**: `postgres`
- **Username / Password / Database**: from your `.env` (`POSTGRES_USERNAME`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`)

Alternatively, use the **DB Explorer** page (`/db-explorer`) built into the dashboard — no SSH tunnel required.

### Querying Postgres on the server

All `docker compose` commands require you to be in the project directory first:

```bash
cd /opt/cora-recap-engine
```

**Interactive psql shell** (run arbitrary queries):
```bash
docker compose exec postgres psql -U postgres -d cora
```
Inside psql: `\dt` lists all tables, `\d <table>` describes a table, `\q` exits.

**One-liner queries** (no interactive session):
```bash
# Row counts
docker compose exec postgres psql -U postgres -d cora -c "SELECT COUNT(*) FROM call_events;"
docker compose exec postgres psql -U postgres -d cora -c "SELECT COUNT(*) FROM scheduled_jobs;"

# Stuck jobs right now
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, job_type, contact_id, run_at,
         EXTRACT(EPOCH FROM (NOW() - run_at))::int AS lag_seconds
  FROM scheduled_jobs
  WHERE status = 'pending' AND run_at < NOW() - INTERVAL '10 minutes'
  ORDER BY run_at;"

# Recent exceptions
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT type, severity, status, created_at
  FROM exceptions ORDER BY created_at DESC LIMIT 20;"

# Active alerts
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT alert_type, severity, message, created_at
  FROM alert_events WHERE status = 'active';"

# Recent call events
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, status, duration_seconds, detected_intent, created_at
  FROM call_events ORDER BY created_at DESC LIMIT 10;"

# Lead state for a specific contact
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT * FROM lead_state WHERE contact_id = '<contact_id>';"
```

**Data persistence** — data is stored in a named Docker volume (`postgres_data`) on the host filesystem, independent of any image or container. It survives all rebuilds and restarts. The only commands that delete it are `docker compose down -v` (explicit volume removal flag) or `docker volume rm cora-recap-engine_postgres_data`. Never pass `-v` to `docker compose down` in production.

### Useful server commands

```bash
# Tail all logs
docker compose logs --follow

# Check queue depths
docker compose exec redis redis-cli llen rq:queue:default
docker compose exec redis redis-cli llen rq:queue:ai

# Restart a single service (no rebuild)
docker compose restart dashboard-api

# Rebuild and restart a single service
docker compose up -d --build frontend

# Check all service health
docker compose ps

# View RQ dashboard (browser at http://localhost:9181 via SSH tunnel)
docker run -p 9181:9181 \
  --network cora-recap-engine_default \
  -e RQ_DASHBOARD_REDIS_URL=redis://redis:6379 \
  eoranged/rq-dashboard

# Flush Redis queues (removes all pending jobs — use with care)
docker compose exec redis redis-cli flushall
```

---

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
| `/queue` | Stuck jobs, expired worker leases, and **Webhook Delivery — 24h** panel: surfaces leads whose outbound call completed but no Synthflow webhook arrived, with inline recovery buttons (VM Left / No Answer / Call Completed) |
| `/alerts` | Threshold alerts (queue lag, error rate, exception spike, worker offline, GHL auth failure) |
| `/voice-performance` | Single-screen voice analytics: KPI sidebar, stacked trends chart, WoW waterfall, efficiency scatter |
| `/ai-performance` | AI quality metrics, intent distribution, consent distribution, intent→outcome table, error trends |
| `/engagement-analysis` | Cross-filter analytics: KPIs by campaign, direction, voice agent, and date range; consent distribution filtered by all active filters |
| `/conversion-funnel` | Funnel visual (Total Calls → Picked Up → Engaged → Booked), step table, drop-off highlight, trend lines |
| `/crm-health` | GHL task and VM update success rates, shadow write count |
| `/lead/[id]` | Per-contact pipeline trace — full job history, shadow flags, failure reasons |
| `/settings` | Runtime settings management — brand config, messaging config, thresholds |
| `/db-explorer` | Embedded SQL query runner — browse all tables with row estimates, write and run queries, download results as CSV (opens in Excel) |

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
| `GET` | `/dashboard/db/tables` | List all Postgres tables with row estimates (no auth required) |
| `POST` | `/dashboard/db/query` | Execute arbitrary SQL and return up to 500 rows as JSON (auth required) |
| `GET` | `/dashboard/lead-lifecycle` | Per-lead journey table (campaign, VM tier, call/SMS/email counts, status) |
| `GET` | `/dashboard/webhook-failures` | Leads whose outbound call completed in the last 24 h with no Synthflow webhook received (excludes terminal/resolved leads) |
| `POST` | `/dashboard/actions/advance-stale-lead` | Manually advance a stale lead: `outcome=voicemail` advances tier or finalizes; `outcome=no_answer` schedules retry or closes (auth required) |
| `POST` | `/dashboard/actions/recover-call-webhook` | Fetch a call from Synthflow by `call_id` and replay the full pipeline (AI analysis + GHL updates) as if the webhook had arrived (auth required) |

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
| `ALERT_DEDUP_WINDOW_SECONDS` | `3600` | Suppress re-fire of the same alert type within this window |
| `SMTP_ENABLED` | `false` | Enable email delivery for threshold alerts |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (587 for TLS/STARTTLS) |
| `SMTP_USE_TLS` | `true` | Enable STARTTLS |
| `SMTP_USERNAME` | — | SMTP login (Gmail: your email address) |
| `SMTP_PASSWORD` | — | SMTP password — **Gmail requires an App Password**, not the account password. Create one at Google Account → Security → 2-Step Verification → App passwords. |
| `ALERT_EMAIL_FROM` | — | Sender address for alert emails |
| `ALERT_EMAIL_TO` | — | Recipient address for alert emails |

### Dashboard background workers

The metrics collector (`collect_metrics_job`) runs every 60 seconds as a self-rescheduling RQ job on the `default` queue. It is started once at worker startup by `start_metrics_scheduler()` (only the `default`/`all` worker role calls this — not every worker process). Each run inserts one row per metric into `system_metrics`, evaluates all alert thresholds, and prunes expired `event_stream` and `system_metrics` rows.

**Alert trigger metric:** `queue_lag_exceeded` fires on `queue_lag_seconds` — the age of the *oldest* overdue pending job — not on raw backlog count. A large backlog of future-dated jobs does not trigger the alert.

See the Alerting / metrics collector troubleshooting section in `directives/spec/11_runbook.md` for common issues.

### Webhook failure recovery

When Synthflow completes an outbound call but its webhook never reaches the API (network drop, burst-concurrency spike, transient Synthflow failure), the lead is left stuck — `launch_outbound_call` completed but no `call_events` row exists and no next job is scheduled.

**Primary tool: Webhook Delivery — 24h panel on Queue Health (`/queue`)**

The panel auto-detects all affected leads within the last 24 hours. Three inline action buttons appear per row — pick based on what Synthflow's Logs page shows for that call:

| Button | When to use | What it does |
|---|---|---|
| **VM Left** | Synthflow shows a voicemail was left | Calls `POST /dashboard/actions/advance-stale-lead` with `outcome=voicemail`. Advances the lead's VM tier (or finalizes at tier 2) and schedules the next call. |
| **No Answer** | Synthflow shows the call did not connect | Calls `POST /dashboard/actions/advance-stale-lead` with `outcome=no_answer`. Schedules a retry; closes the lead on a second consecutive no-answer. |
| **Call Completed** | Synthflow shows a completed call with a transcript | Expands a call_id input. Enter the Synthflow call_id (from the Synthflow Logs page — not the internal job_id), then press Enter or "Fetch →". Calls `POST /dashboard/actions/recover-call-webhook`, which fetches the call from Synthflow and schedules a `process_call_event` job. The full pipeline runs: AI analysis, intent classification, GHL updates. |

A row disappears from the panel after a successful action because the lead's status becomes terminal/closed or a new pending job is created — this is expected.

**For incidents older than 24 hours:** use the detection SQL query and bulk recovery scripts documented in `directives/spec/dashboard/11_runbook.md` → "Leads stuck mid-voicemail sequence".

**Dashboard token required:** all three actions require the Bearer token to be set in Settings (`/settings` → Dashboard Token card). Without it, the buttons return 403.

### Feature documentation

See [`docs/dashboard-feature-usecases.md`](docs/dashboard-feature-usecases.md) for a detailed description of every page, every section within each page, and the specific business question each feature answers.