# spec/dashboard/03_constraints.md

## Musts

- **Postgres is the only source of truth.** All metrics, KPIs, event history, and operator audit records must be derived from or written to Postgres. No derived-only in-memory state survives a restart.
- **Redis is the execution layer only.** Redis Pub/Sub may carry real-time events. Redis must not be used as a persistent store for any dashboard metric or alert state.
- **No external observability tools.** Prometheus, Grafana, Datadog, New Relic, Sentry, and equivalent paid or self-hosted tools are explicitly out of scope.
- **No external alerting tools.** Alerting is email-only via SMTP. Slack, PagerDuty, OpsGenie, and webhook-based tools are out of scope.
- **The existing Streamlit dashboard must not be modified.** File `execution/dashboard.py` is read-only for this project. No new imports, no schema changes that would break it, no shared state.
- **All operator actions must go through the API.** Frontend must never write directly to Postgres. Every retry, cancel, finalize, resolve, or ignore must POST to a dashboard API endpoint that enforces auth, idempotency, and audit logging.
- **All operator actions must be audit-logged.** Every action writes one row to `audit_log` with `entity_type`, `entity_id`, `action`, `operator_id`, and `context_json`. No exceptions.
- **Shadow mode must be respected.** Dashboard actions that would trigger an outbound action (retry of a send_sms job, etc.) must honor `SHADOW_MODE_ENABLED`. In shadow mode, retried outbound jobs complete normally but produce shadow rows, not real sends.
- **No GHL or Synthflow writes from the dashboard API.** The dashboard API does not call GHL or Synthflow directly. Operator actions only write to Postgres (schedule a job, update exception status, update lead_state). The worker picks up and executes the resulting jobs.
- **Idempotency must be maintained.** Retrying an exception must not create duplicate `scheduled_jobs` rows. Use the existing `dedupe_key` mechanism on `scheduled_jobs`.
- **No duplicate CRM writes.** Force-finalize must check `task_events` before writing; the API must not bypass existing dedupe guards.
- **Authentication is required for all operator actions.** Bearer token auth (same `SECRET_KEY` as the existing dashboard). Read-only endpoints may optionally require auth depending on deployment.
- **Local development must work without cloud dependencies.** SMTP can be stubbed (`SMTP_ENABLED=false`). Redis Pub/Sub is optional (polling fallback). No cloud database, no external services required.

## Must-nots

- Must not write to `call_events`, `classification_results`, `summary_results`, or `outbound_messages` from the dashboard API. These tables are written only by the worker.
- Must not call OpenAI from the dashboard API. AI generation is worker-only.
- Must not expose raw GHL API keys, OpenAI keys, or any credential in API responses.
- Must not expose the full `raw_payload_json` field from `call_events` in public API responses — strip to a safe summary (`payload_summary`) containing only non-sensitive fields (`call_id`, `campaign_name`, `status`, `duration_seconds`, `detected_intent`).
- Must not implement real-time via long-polling with held DB connections. Use Redis Pub/Sub (preferred) or short-interval client-side polling against a lightweight events endpoint.
- Must not introduce schema changes that add non-nullable columns to existing tables without defaults. All migrations must be backwards-compatible.
- Must not bypass the `claim_job` / `complete_job` / `fail_job` lease mechanism when enqueueing retry jobs. Use `schedule_job()` from `app/worker/scheduler.py`.
- Must not deploy the Next.js frontend and the dashboard API on the same port. Frontend: port 3000. Dashboard API: port 8001 (separate from the main API on port 8000).

## Preferences

- Prefer Recharts for charts (React-native, no paid tier, good Postgres-derived data fit).
- Prefer server-side pagination for all list views (limit/offset) over client-side filtering of large result sets.
- Prefer the existing `app/services/dashboard.py` service layer for operator actions rather than duplicating logic in a new file.
- Prefer named query parameters (`:param`) over positional `$1` in raw SQL for readability and maintainability.
- Prefer JSONB for flexible context fields; use strict typed columns for all indexed and filtered fields.
- Prefer connection pooling via existing `get_sync_engine()` rather than creating new engine instances.

## Escalation triggers

- If a required Postgres query exceeds 3 seconds in testing under realistic data volume (1000+ leads, 10 000+ call_events), escalate before shipping: add index or rewrite the query.
- If Redis Pub/Sub introduces more than 500 ms of latency in local testing, switch to polling-only mode and document the decision in ADR-0003.
- If SMTP integration requires a paid relay (e.g., SendGrid), escalate to product owner. The default assumption is a self-managed SMTP server or a free-tier relay such as Gmail SMTP app password.
- If Next.js build complexity requires a dedicated DevOps pipeline not currently in place, escalate before implementation begins.
- If adding `system_metrics`, `event_stream`, or `alert_events` tables causes migration conflicts with in-progress schema changes, escalate before applying.
