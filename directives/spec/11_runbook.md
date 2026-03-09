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

## Troubleshooting
- duplicate task → inspect `dedupe_key` in `call_events` and `task_events`
- missing summary → inspect `summary_results.summary_consent` and transcript length
- lost callback → inspect `scheduled_jobs` where `job_type='synthflow_callback'`
- GHL write failure → inspect `GHL_API_KEY` and `GHL_LOCATION_ID` in `.env`
- exception queue growing → use dashboard retry/cancel/finalize actions
- expired job leases → `recover_expired_claims()` runs on worker restart

## Migration commands
```bash
alembic upgrade head     # apply all migrations
alembic current          # check current revision
alembic downgrade -1     # roll back one step
```

## Reporting runbook notes
- Validate KPI values against authoritative SQL queries after schema or logic changes.
- Validate filtering and cross-filtering behavior after reporting-model changes.
- Do not treat missing tooltip or drill-down behavior as a defect in the current phase.
- Reporting source of truth: `fact_call_activity` and `fact_kpi_daily` views in Postgres.
- Google Sheets is NOT the reporting source — visual inspection only.
