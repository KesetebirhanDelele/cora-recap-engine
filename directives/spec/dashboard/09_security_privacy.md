# spec/dashboard/09_security_privacy.md

## Authentication

### Read-only routes
Controlled by `DASHBOARD_READ_AUTH_REQUIRED` (default: `false` in development, `true` in production).
When `true`: Bearer token required on `GET /dashboard/*` routes.
When `false`: read routes are unauthenticated. Only appropriate for local development behind a firewall.

### Operator action routes
`POST /dashboard/actions/*` always require a valid Bearer token regardless of `DASHBOARD_READ_AUTH_REQUIRED`.
Token validation uses `require_dashboard_auth()` from `app/api/deps.py`.
The secret is `SECRET_KEY` from settings (same as existing dashboard).

### Operator identity
The `X-Operator-Id` request header carries the operator's human-readable identifier (email or username).
This value is written to `audit_log.operator_id` for all operator actions.
If absent: `operator_id` defaults to `"unknown"` but the action is still permitted (auth token is the gate).
Frontend must always send `X-Operator-Id` for all POST requests.

### WebSocket
WebSocket connections to `ws://localhost:8001/ws` are not auth-gated.
No reverse proxy is used in this phase. Direct connection from the browser to the dashboard API.

---

## Data exposure rules

### What the API may return
- `contact_id`, `normalized_phone` (phone number — acceptable for internal operator use)
- `campaign_name`, `status`, `ai_campaign_value`, `detected_intent`
- `job_type`, `run_at`, `status`, `duration_ms`, `failure_reason` for scheduled jobs
- `exception` type, severity, `context_json` (bounded — must not include raw API credentials)
- `payload_summary` from `call_events`: only `call_id`, `campaign_name`, `call_status`, `duration_seconds`, `detected_intent`
- Generated message body from `outbound_messages.body` and `shadow_actions.payload.message_body`
- Alert type, value, threshold, timestamp

### What the API must never return
- `raw_payload_json` in full from `call_events` — this field contains Synthflow-signed payloads with internal routing data and possibly sensitive contact fields
- GHL API keys or OpenAI API keys from settings
- Postgres `DATABASE_URL` or Redis `REDIS_URL` credentials
- Full transcripts via the health or metrics endpoints. Transcripts may only appear in the pipeline trace step when explicitly requested by `GET /dashboard/lead/{id}/trace`, and only the first 500 characters
- Contents of `parameters_hard_coded` from `executed_actions` (contains GHL location IDs and auth tokens)

---

## PII handling

### Contact data
- Phone numbers and contact IDs are considered PII under CCPA/state privacy laws applicable to the business.
- The dashboard API does not log phone numbers to stdout except at DEBUG level. Production log level must be INFO or higher.
- Pipeline trace and lead journey views are accessible only to authenticated operators.
- No contact PII is written to `event_stream.payload` or `system_metrics.labels`.

### Transcript content
- Transcripts contain spoken conversation and are treated as sensitive.
- `event_stream` must never include transcript content.
- Pipeline trace includes at most 500 characters of transcript in the `payload_summary` and only when explicitly included via a query param (`?include_transcript=true`, default false).

### Email alert content
- Alert emails must not include contact IDs, phone numbers, or transcript excerpts.
- Alerts reference metric names, values, and thresholds only.

---

## Transport security

- Current phase: HTTP on localhost. No TLS termination required.
- WebSocket uses `ws://localhost:8001/ws`. No `wss://` required in this phase.
- SMTP uses STARTTLS on port 587 (`SMTP_USE_TLS=true`). Gmail app password authentication.
- Cookies are not used. All auth is header-based (Bearer token).

---

## Environment variables

New variables introduced by this system:

| Variable | Required | Default | Description |
|---|---|---|---|
| `DASHBOARD_READ_AUTH_REQUIRED` | No | `false` | Require auth on GET routes |
| `ALERT_EMAIL_FROM` | Yes (prod) | — | From address for alert emails |
| `ALERT_EMAIL_TO` | Yes (prod) | — | Comma-separated recipient list |
| `SMTP_HOST` | Yes | `smtp.gmail.com` | Gmail SMTP server |
| `SMTP_PORT` | No | `587` | SMTP port (STARTTLS) |
| `SMTP_USERNAME` | Yes | — | Gmail address (from .env) |
| `SMTP_PASSWORD` | Yes | — | Gmail app password (from .env) |
| `SMTP_USE_TLS` | No | `true` | Enable STARTTLS |
| `SMTP_ENABLED` | No | `true` | Set `false` to suppress sends in dev |
| `ALERT_QUEUE_LAG_THRESHOLD_SECONDS` | No | `300` | Queue lag alert threshold |
| `ALERT_ERROR_RATE_THRESHOLD` | No | `0.20` | Error rate alert threshold |
| `ALERT_EXCEPTION_SPIKE_THRESHOLD` | No | `10` | Open exception count threshold |
| `ALERT_DEDUP_WINDOW_SECONDS` | No | `3600` | Seconds before re-alerting same type |
| `METRICS_COLLECTION_INTERVAL_SECONDS` | No | `60` | Metrics collector cycle interval |
| `EVENT_STREAM_RETENTION_DAYS` | No | `7` | Days before event_stream rows purged |
| `SYSTEM_METRICS_RETENTION_DAYS` | No | `30` | Days before system_metrics rows purged |

All secrets (`SMTP_PASSWORD`, etc.) must be provided via environment variables. They must never be committed to the repository.

---

## Audit and non-repudiation

- `audit_log` is append-only. No dashboard API route may update or delete audit log rows.
- Operator ID is taken from the `X-Operator-Id` header; the API does not validate that this ID matches any user registry. The responsibility for accurate operator identification lies with the frontend application.
- In a future phase, replace `X-Operator-Id` with a JWT claim if an identity provider is introduced.
