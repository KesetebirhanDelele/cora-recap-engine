# spec/dashboard/12_open_questions.md

All questions are resolved for the current implementation phase (local development environment). Decisions are final unless explicitly reopened.

---

## Resolved — blocking

### OQ-01 — SMTP relay provider
**Status**: RESOLVED
**Decision**: Gmail SMTP with app password.
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=<from .env>
SMTP_PASSWORD=<from .env>
ALERT_EMAIL_TO=<from .env>
```
Sufficient for local and early production alerting volume. No SES or Mailgun required.

### OQ-02 — Dashboard host and port
**Status**: RESOLVED
**Decision**: Localhost deployment, no reverse proxy.
```
API_BASE_URL=http://localhost:8000
DASHBOARD_API_URL=http://localhost:8001
FRONTEND_URL=http://localhost:3000
WS_URL=ws://localhost:8001/ws
ALLOW_ORIGINS=http://localhost:3000
```
CORS restricted to localhost frontend. No nginx or load balancer in this phase.

### OQ-03 — Authentication model
**Status**: RESOLVED
**Decision**: Shared `SECRET_KEY` Bearer token + `X-Operator-Id` header. Matches existing system behavior. No JWT or identity provider required. `audit_log.operator_id` is populated from the `X-Operator-Id` header.

---

## Resolved — pre-Phase 3

### OQ-04 — Redis version and Pub/Sub compatibility
**Status**: RESOLVED
**Decision**: `redis>=4.5` using the built-in `redis.asyncio` module for Pub/Sub. The deprecated `aioredis` package must NOT be used. Existing RQ sync usage is unaffected.

### OQ-05 — WebSocket behind reverse proxy
**Status**: RESOLVED
**Decision**: No reverse proxy in this phase. Direct WebSocket connection to `ws://localhost:8001/ws`. No nginx `Upgrade` header configuration required.

---

## Resolved — pre-Phase 5

### OQ-06 — Transcript exposure policy
**Status**: RESOLVED
**Decision**: Limited, opt-in. Default response excludes transcript. `?include_transcript=true` returns at most 500 characters. Full transcripts must never appear in API responses or system logs.

---

## Resolved — deferred to v2

### OQ-07 — Booked appointments KPI
**Status**: RESOLVED (deferred)
**Decision**: Use `enrolled_count` from `classification_results` as the proxy for booked appointments. No GHL appointment integration in v1.

### OQ-08 — Multi-location support
**Status**: RESOLVED (out of scope v1)
**Decision**: Single `GHL_LOCATION_ID` assumed. Schema must not introduce constraints that would block future multi-location extension (no hardcoded location columns, no single-row settings tables).

### OQ-09 — Dashboard access logging
**Status**: RESOLVED (deferred)
**Decision**: Not implemented in v1. Existing `audit_log` covers operator actions only. Page view logging deferred.

### OQ-10 — Alert escalation
**Status**: RESOLVED (deferred)
**Decision**: Single-recipient alerting only (`ALERT_EMAIL_TO`). Escalation logic deferred. `acknowledged_by` and `acknowledged_at` columns in `alert_events` are retained for future use.

---

## Summary of all configuration values

```env
# Dashboard API
DASHBOARD_API_URL=http://localhost:8001
DASHBOARD_READ_AUTH_REQUIRED=false
ALLOW_ORIGINS=http://localhost:3000

# Frontend
FRONTEND_URL=http://localhost:3000
WS_URL=ws://localhost:8001/ws

# SMTP — Gmail
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=<gmail address>
SMTP_PASSWORD=<gmail app password>
SMTP_ENABLED=true                    # false in dev to suppress sends
ALERT_EMAIL_FROM=<gmail address>
ALERT_EMAIL_TO=<recipient address>

# Alert thresholds
ALERT_QUEUE_LAG_THRESHOLD_SECONDS=300
ALERT_ERROR_RATE_THRESHOLD=0.20
ALERT_EXCEPTION_SPIKE_THRESHOLD=10
ALERT_DEDUP_WINDOW_SECONDS=3600

# Metrics collector
METRICS_COLLECTION_INTERVAL_SECONDS=60
EVENT_STREAM_RETENTION_DAYS=7
SYSTEM_METRICS_RETENTION_DAYS=30
```
