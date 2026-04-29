# spec/11_runbook.md

## Setup
1. Provision Postgres.
2. Provision Redis.
3. Configure API and worker services.
4. Configure GHL, Synthflow, and OpenAI credentials in `.env`.
5. Apply schema/migrations: `alembic upgrade head`
6. Prompt registry is auto-seeded at import time (no manual step).
7. Campaign tier policies are configured via `.env` variables.

Note: Google Sheets shadow sync is out of scope. No Sheets setup required.

---

## Production Deployment (Hetzner Cloud)

### Infrastructure
- Hetzner CX22 (2 vCPU, 4 GB RAM, Ubuntu 22.04)
- Docker CE + Docker Compose plugin
- All services run in Docker Compose: postgres, redis, migrate, api, dashboard-api, frontend, worker-default, worker-ai, worker-callbacks, worker-retries

### Key .env values for production

```
APP_ENV=production
DATABASE_URL=postgresql+psycopg2://postgres:<PASSWORD>@postgres:5432/cora
REDIS_HOST=redis
DASHBOARD_API_URL=http://<server-ip>:8001
WS_URL=ws://<server-ip>:8001/ws
ALLOW_ORIGINS=http://<server-ip>:3000
SECRET_KEY=<strong-random-secret>
WEBHOOK_SHARED_SECRET=<strong-random-secret>

# Synthflow — per-campaign Make Call webhook URLs (required for outbound calls)
SYNTHFLOW_API_KEY=<synthflow-api-key>
SYNTHFLOW_LAUNCH_WORKFLOW_URL_New=https://workflow.synthflow.ai/api/v1/webhooks/<new-lead-webhook-id>
SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold=https://workflow.synthflow.ai/api/v1/webhooks/<cold-lead-webhook-id>
```

Synthflow routing rules:
- `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New` — triggers the New Lead Make Call workflow (`p6ihFj7HmplXM2WiuVsaC`)
- `SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold` — triggers the Cold Lead Make Call workflow (`33J546NiXxUUIRCbywNVH`)
- The single legacy `SYNTHFLOW_LAUNCH_WORKFLOW_URL` field no longer exists — both URLs are required
- `JylDXjF8QB0Skr5cQzGGm` must never be used — it is a test workflow that silently drops calls

Rules:
- `DATABASE_URL` must use Docker service name `postgres:5432` (not `localhost` or `host.docker.internal`)
- `DASHBOARD_API_URL` must be the public server IP — baked into Next.js bundle at build time
- `docker-compose.override.yml` must NOT be present on the server — it is local dev only
- Settings validator blocks boot if `SECRET_KEY` or `WEBHOOK_SHARED_SECRET` is `changeme` when `APP_ENV=production`

### Deploy commands

```bash
cd /opt/cora-recap-engine
git pull origin main
docker compose up -d --build
docker compose logs migrate       # verify exits 0
docker compose ps                 # all services healthy/running
```

### Synthflow webhook URL

```
http://<server-ip>:8000/v1/webhooks/calls
```

Use `http://` not `https://` unless a reverse proxy with TLS is in place.

Payload note: Synthflow HTTP step wraps the payload under `{"data": {...}}`. The normalizer at `app/api/routes/webhooks.py` unwraps this automatically.

### Hetzner firewall ports (required)

Open inbound TCP in Hetzner Cloud Console → Firewalls:
- `8000` — Synthflow webhook + main API
- `8001` — Dashboard API (browser)
- `3000` — Next.js frontend

---

## Local run

### Core pipeline (port 8000)
1. Start Postgres and Redis.
2. Verify `.env` credentials (GHL_API_KEY, OPENAI_API_KEY, SYNTHFLOW_API_KEY).
3. Apply migrations: `alembic upgrade head` (current head: 0012)
4. Start API service: `cora-api` or `uvicorn app.main:app --reload`
5. Start worker service: `cora-worker`
6. Post test webhook events to `POST /v1/webhooks/calls`.

### Dashboard v2 (port 8001 + 3000)
1. Start the dashboard API (separate process):
   ```bash
   uvicorn app.api.dashboard_main:app --port 8001 --reload
   ```
2. Install frontend dependencies (one-time):
   ```bash
   cd dashboard-ui && npm install
   ```
3. Start the Next.js frontend:
   ```bash
   npm run dev   # http://localhost:3000
   ```
4. The metrics collector (`collect_metrics_job`) starts automatically with the worker — no manual step needed.

Required `.env` additions for Dashboard v2:
```
SECRET_KEY=your-dev-secret
DASHBOARD_READ_AUTH_REQUIRED=false
ALLOW_ORIGINS=http://localhost:3000
# Frontend: dashboard-ui/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8001
NEXT_PUBLIC_DASHBOARD_TOKEN=your-dev-secret
```

## Monitoring dashboard
Requires only Postgres (API/worker do not need to be running).

```bash
# One-time install
pip install streamlit

# Launch (reads DATABASE_URL from .env)
streamlit run execution/dashboard.py
```

Opens at http://localhost:8501.

Sections (10):
1. **Overview** — system health tiles and charts
2. **Campaign Overview** — all active leads with next action; filterable by date window
3. **Trends** — call volume and rate trends by campaign
4. **Recent Calls** — call event log with transcript preview
5. **Lead State** — filterable lead table
6. **Shadow Actions** — intercepted actions (visible when `SHADOW_MODE_ENABLED=true`)
7. **Scheduled Jobs** — job queue state
8. **Exceptions** — operator exception queue
9. **Contact Drill-Down** — raw data view per contact_id
10. **Lead Journey** — per-lead timeline filterable by phone number

### Using Lead Journey
Enter a phone number in E.164 format (`+1XXXXXXXXXX`). The page shows:
- Summary card: campaign, status, VM tier, DNC flag, next scheduled action
- Chronological timeline: voicemail calls, answered calls (with transcript preview), SMS/email, campaign switches
- Campaign switches sourced from `audit_log` (written automatically by `apply_campaign_switch()`)
- When shadow mode is on: SMS and outbound call touchpoints are in `shadow_actions`, not `outbound_messages` — they do not appear in the Lead Journey timeline until shadow mode is added as a timeline source

If `alembic current` or the dashboard fail with `host "192.168.1.x" ... no pg_hba.conf entry`, a shell environment variable is overriding `.env`:
```bash
# PowerShell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
# bash
unset DATABASE_URL
```

The endpoint to paste into Synthflow is:

POST {your_base_url}/v1/webhooks/calls
For example, in development: http://localhost:8000/v1/webhooks/calls

In production, replace {your_base_url} with your deployed API URL (set as API_BASE_URL in .env).

This is the webhook intake route at app/api/routes/webhooks.py:18 — it accepts any POST payload, extracts the call_id, and enqueues a process_call_event job for the worker to process.

## Testing
```bash
# Unit tests (no credentials required)
python -m pytest tests/unit/ tests/evals/ -v

# Regression suite specifically
python -m pytest tests/evals/test_regression_suite.py -v

# E2E scenarios
python -m pytest tests/unit/test_e2e_scenarios.py -v

# Integration tests (requires Postgres + Redis)
INTEGRATION_TESTS=1 pytest tests/integration/

# Lint
ruff check app/ tests/ migrations/
```

## Dashboard operator actions
All actions require: `Authorization: Bearer {SECRET_KEY}`

| Action | Endpoint |
|---|---|
| List exceptions | `GET /v1/exceptions?status=open` |
| Exception detail | `GET /v1/exceptions/{id}` |
| Retry immediately | `POST /v1/exceptions/{id}/retry-now` |
| Retry with delay | `POST /v1/exceptions/{id}/retry-delay` body: `{"delay_minutes": 60}` |
| Cancel future jobs | `POST /v1/exceptions/{id}/cancel-future-jobs` |
| Force finalize | `POST /v1/exceptions/{id}/force-finalize` |

Operator actions are audit-logged in the `audit_log` table.
Concurrent actions: first wins, second gets HTTP 409.

## Live-call intent routing

After AI analysis on a completed call, `detect_intent()` is called with the transcript, `executed_actions` (from `call_event.raw_payload_json`), and `duration_seconds`. Detected signals trigger `handle_intent()` via the same pipeline as voicemail-transcript intents.

### Signal reference

| Signal | Primary trigger | Handler outcome |
|---|---|---|
| `human_transfer_request` | Transcript: "talk to a real person", "transfer me", etc. OR `executed_actions` transfer attempt | Status → `human_transfer`; +2 h follow-up scheduled if transfer unconfirmed |
| `failed_booking` | Transcript: "didn't work", "couldn't book", etc. OR `executed_actions` booking failure | +4 h retry scheduled, campaign unchanged |
| `partial_engagement` | No strong intent matched + call duration < 120 s | +2 h retry scheduled, campaign unchanged; after 2 retries → Cold Lead campaign |
| `low_confidence_audio` | Transcript < 5 chars or noise/inaudible only | Status → `cold`; enters Cold Lead campaign (cancels jobs, resets tier, schedules first outbound) |

`partial_engagement` only fires when `duration_seconds` is present in the payload and below threshold. Calls with no duration signal fall through to `None`.

### Operator actions for live-call scenarios

- **Lead stuck in `human_transfer` status** — the human agent did not complete enrollment. Check if a follow-up `launch_outbound_call` is pending in `scheduled_jobs`. If the lead should re-enter the AI campaign, use `force-finalize` or manually update `lead_state.status` and schedule a new call via the test endpoint.
- **Lead unexpectedly moved to Cold Lead** — caused by `low_confidence_audio` detection. Check transcript in `call_events` and `classification_results`. If the classification was incorrect, use `cancel-future-jobs` to stop the queued Cold Lead call, then manually set `lead_state.campaign_name = 'New Lead'` and `ai_campaign_value = NULL`.
- **Duplicate follow-up calls scheduled** — `_schedule_outbound_call` in `intent_actions.py` has an idempotency guard; inspect `scheduled_jobs` for `launch_outbound_call` rows with status `pending` for the contact. Cancel duplicates via the dashboard `cancel-future-jobs` endpoint.

## Nurture scheduler

The nurture scheduler runs every 5 minutes and graduates `status='nurture'` leads whose `next_action_at` has passed into the Cold Lead campaign.

- `run_nurture_scheduler` job appears in `scheduled_jobs` with `entity_id='nurture_scheduler'`
- On worker startup, `ensure_scheduled()` creates the first job automatically
- Individual lead failures are isolated — one bad row does not stop the batch
- If no `run_nurture_scheduler` job is pending, the worker may have been restarted without running `ensure_scheduled()` — restart the worker or manually insert a job

## Troubleshooting

### Production / Docker Compose issues
- `migrate` fails with `host.docker.internal` → `docker-compose.override.yml` is present on server or `.env` has `DATABASE_URL` pointing to localhost; remove override file, fix `DATABASE_URL` to use `postgres:5432`
- frontend shows `Couldn't find pages or app directory` → dev Dockerfile used; `docker compose build --no-cache frontend && docker compose up -d frontend`
- `TypeError: Failed to fetch` on all dashboard pages → `DASHBOARD_API_URL` baked as localhost at build time; set correct server IP in `.env` then rebuild frontend with `--no-cache`
- `TypeError: Failed to fetch` persists after rebuild → port 8001 blocked by Hetzner firewall; add inbound TCP 8001 rule in Hetzner Cloud Console
- CORS errors in browser → `ALLOW_ORIGINS` doesn't match browser origin; set `ALLOW_ORIGINS=http://<server-ip>:3000` in `.env`, restart `dashboard-api`
- Synthflow POST returns connection error → URL uses `https://`; change to `http://`
- Synthflow POST returns 422 `missing_call_id` → payload wrapped under `data` key and not unwrapped; ensure latest code is deployed (normalizer unwraps `{"data": {...}}` envelope automatically)
- all dashboard data shows zeros → no call events in DB yet; normal on fresh deploy

### Application issues
- duplicate task → inspect `dedupe_key` in `call_events` and `task_events`
- missing summary → inspect `summary_results.summary_consent` and transcript length
- lost callback → inspect `scheduled_jobs` where `job_type='process_voicemail_tier'`
- GHL write failure → inspect `GHL_API_KEY` and `GHL_LOCATION_ID` in `.env`
- exception queue growing → use dashboard retry/cancel/finalize actions
- expired job leases → `recover_expired_claims()` runs on worker restart
- outbound calls/SMS/email not sending → check `SHADOW_MODE_ENABLED`; if `true`, actions are intercepted and logged to `shadow_actions` instead of executed
- SMS/email not sending despite shadow mode off → check `inbound_messages` and `lead_state.last_replied_at`; reply detection suppresses sends if either signal is set
- nurture lead not graduating to Cold Lead → check `next_action_at` in `lead_state`; check `run_nurture_scheduler` job exists in `scheduled_jobs` with `status=pending`
- lead unexpectedly moved to Cold Lead after repeated answered calls → `partial_engagement` retry cap reached (2 retries); check `scheduled_jobs` count for `intent_reason='partial_engagement'` on the contact
- `alembic current` or dashboard fails with `host.docker.internal` pg_hba error → a shell `DATABASE_URL` env var is overriding `.env`; remove it with `Remove-Item Env:DATABASE_URL`
- lead moved to Cold Lead unexpectedly → check `call_events.transcript` length; may have triggered `low_confidence_audio` (transcript < 5 chars)
- lead stuck in `human_transfer` status → check `scheduled_jobs` for pending follow-up; see Live-call intent routing section above
- lead showing wrong campaign in Campaign Overview or Lead Journey → check `call_events.raw_payload_json->>'Agent'`; if it contains 'ColdLead' but `lead_state.campaign_name = 'New Lead'`, the lead was processed before the `Agent`-field normalisation fix; correct manually or re-process
- lead not visible in Lead Journey by phone number → `lead_state.normalized_phone` may be null (lead created by `update_lead_state` before the normalised_phone fix on 2026-03-31); Lead Journey will fall back to `call_events.raw_payload_json` phone match, but if no call_events exist the lead won't resolve
- voicemail tier not advancing after shadow mode was on → shadow mode intercepted `launch_outbound_call` without placing a real call; Synthflow never sent a callback; re-trigger from tier 0 once shadow mode is disabled
- **leads stuck mid-voicemail sequence with no pending job and no call_event** → Synthflow HTTP step webhook drop, either from burst concurrency or transient failure. Diagnose with the detection query in `spec/14_synthflow_integration_addendum.md`. Use `execution/recover_webhook_drop_20260428.py` with a Synthflow CSV export — it handles both tier advancement (Path B) and AI queuing for completed calls (Path A). Always dry-run first. The NOT EXISTS window in both the recovery script and `get_webhook_failures()` is 7 days to prevent re-detecting already-recovered contacts on re-runs.
- **large count of leads stuck at same minute** (burst pattern) → all rescheduled calls landed at `run_at = window_start` exactly; `_compute_window_run_at()` was not used. Confirm fix is deployed, then run recovery scripts above.
- **burst pattern repeats at +24h or +48h after a prior burst** → voicemail retry callbacks scheduled by `_schedule_retry_outbound_call()` use `now + delay_minutes` directly; they inherit the original burst timestamp. `_compute_window_run_at()` does not apply here. Spread manually using the slot redistribution SQL in `spec/14_synthflow_integration_addendum.md` before the affected slots fire. Verify with: `SELECT date_trunc('hour', run_at) + INTERVAL '5 min' * FLOOR(EXTRACT(minute FROM run_at)/5) AS slot, COUNT(*) FROM scheduled_jobs WHERE job_type='launch_outbound_call' AND status='pending' AND run_at >= NOW() GROUP BY slot ORDER BY slot;` — any slot > 4 needs redistribution.
- Lead Journey shows only 1 event despite multiple outreach attempts → SMS and outbound calls in shadow mode are in `shadow_actions`, not `outbound_messages`; they are not currently displayed in Lead Journey timeline

### Alerting / metrics collector issues

- **No rows in `system_metrics`, dashboard shows 0 active alerts** → `collect_metrics_job` never ran. Check worker-default startup logs for: `Could not ensure metrics scheduler on startup`. If present, confirm the `collect_metrics` job exists in `scheduled_jobs` with `status='pending'`; if missing, restart `worker-default` (it calls `start_metrics_scheduler()` on boot).

- **Dashboard shows large backlog in Queue Health but 0 active alerts** → The `queue_lag_exceeded` alert fires on `queue_lag_seconds` (age of the oldest overdue pending job), **not** on backlog count. If all pending jobs are future-dated (e.g. voicemail retries scheduled minutes ahead), `queue_lag_seconds` = 0 and no alert fires. Use the diagnostic query below to confirm:
  ```sql
  SELECT EXTRACT(EPOCH FROM (NOW() - MIN(run_at)))::int AS lag_s
  FROM scheduled_jobs WHERE status = 'pending' AND run_at <= NOW();
  ```

- **Scheduler loop logs only `no handler for job_type=...` warnings and nothing else processes** → A job type exists in DB (`scheduled_jobs`) but is missing from `_JOB_QUEUE_ATTRS` or `get_job_registry()` in `app/worker/main.py`. The scheduler loop's 100-job batch is consumed by unhandled jobs, starving other job types. Add the missing job type to both maps and redeploy `worker-default`.

- **Alert fires on dashboard but no email received** → Check worker-default logs for `SMTP send failed`. Most common cause: Gmail rejects `SMTP_PASSWORD` with `535 Username and Password not accepted` when the value is the account password instead of an App Password. Fix:
  1. Go to Google Account → Security → 2-Step Verification → App passwords
  2. Generate a new App Password ("Mail" / "Other")
  3. Update `SMTP_PASSWORD` in `/opt/cora-recap-engine/.env` with the 16-character App Password (no spaces)
  4. `docker compose restart worker-default`

- **Alert email delivered but alert not cleared** → Alerts auto-resolve on the next metrics cycle (≤60 s) when the metric drops below threshold. If the metric remains above threshold, the alert stays active — this is correct behavior. The dashboard Alerts page shows current status and last-seen time.

## Migration commands
```bash
alembic upgrade head     # apply all migrations (current head: 0012)
alembic current          # check current revision
alembic downgrade -1     # roll back one step
```

Migrations (in order):
- `0001` — 8 core tables (lead_state, call_events, scheduled_jobs, exceptions, classification_results, summary_results, task_events, inbound_messages)
- `0002` — reporting views (`fact_call_activity`, `fact_kpi_daily`)
- `0003` — `audit_log` table
- `0004` — Synthflow fields on `call_events` (model_id, lead_name, agent_phone_number, timeline, telephony_*)
- `0005` — intent fields on `lead_state` (status, do_not_call, invalid, preferred_channel, next_action_at, last_replied_at)
- `0006` — `outbound_messages` and `inbound_messages` tables
- `0007` — `shadow_actions` table
- `0008` — `detected_intent` column on `call_events`
- `0009` — `app_config` table for runtime settings
- `0010` — brand and messaging configuration fields
- `0011` — `system_metrics`, `event_stream`, `alert_events` tables (required for Dashboard v2 metrics collector, event feed, and alerting)
- `0012` — `voice_agent` column on `call_events` (used by Campaign Overview campaign resolution fallback)

## Reporting runbook notes
- Validate KPI values against authoritative SQL queries after schema or logic changes.
- Validate filtering and cross-filtering behavior after reporting-model changes.
- Do not treat missing tooltip or drill-down behavior as a defect in the current phase.
- Reporting source of truth: `fact_call_activity` and `fact_kpi_daily` views in Postgres.
- Google Sheets is NOT the reporting source — visual inspection only.
