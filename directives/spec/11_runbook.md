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

## Local run
1. Start Postgres and Redis.
2. Verify `.env` credentials (GHL_API_KEY, OPENAI_API_KEY, SYNTHFLOW_API_KEY).
3. Start API service: `cora-api` or `uvicorn app.main:app --reload`
4. Start worker service: `cora-worker`
5. Post test webhook events to `POST /v1/webhooks/calls`.

## Monitoring dashboard
Requires only Postgres (API/worker do not need to be running).

```bash
# One-time install
pip install streamlit

# Launch (reads DATABASE_URL from .env)
streamlit run execution/dashboard.py
```

Opens at http://localhost:8501. Sections: Overview, Recent Calls, Lead State, Shadow Actions, Scheduled Jobs, Exceptions, Contact Drill-Down.

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
- duplicate task → inspect `dedupe_key` in `call_events` and `task_events`
- missing summary → inspect `summary_results.summary_consent` and transcript length
- lost callback → inspect `scheduled_jobs` where `job_type='synthflow_callback'`
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

## Migration commands
```bash
alembic upgrade head     # apply all migrations (current head: 0007)
alembic current          # check current revision
alembic downgrade -1     # roll back one step
```

Migrations (in order):
- `0001` — 8 core tables
- `0002` — reporting views (`fact_call_activity`, `fact_kpi_daily`)
- `0003` — `audit_log` table
- `0004` — Synthflow fields on `call_events`
- `0005` — intent fields on `lead_state`
- `0006` — `outbound_messages` and related tables
- `0007` — `shadow_actions` table

## Reporting runbook notes
- Validate KPI values against authoritative SQL queries after schema or logic changes.
- Validate filtering and cross-filtering behavior after reporting-model changes.
- Do not treat missing tooltip or drill-down behavior as a defect in the current phase.
- Reporting source of truth: `fact_call_activity` and `fact_kpi_daily` views in Postgres.
- Google Sheets is NOT the reporting source — visual inspection only.
