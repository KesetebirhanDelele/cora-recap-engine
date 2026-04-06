# spec/dashboard/06_architecture.md

## Overview
Three-layer architecture: Next.js frontend → FastAPI dashboard API → Postgres. Redis Pub/Sub bridges the API and frontend for real-time delivery. A background metrics collector writes aggregated health data to Postgres on a 60-second cycle. An alert evaluator runs inside the same cycle and dispatches email via SMTP.

The system shares Postgres and Redis with the existing worker and API but introduces no new infrastructure dependencies beyond an SMTP relay.

---

## Component map

```
┌────────────────────────────────────────────────────────────────┐
│  Browser (Next.js)                                             │
│   ├── HTTP polling   →  Dashboard API  (port 8001)            │
│   └── WebSocket      →  Dashboard API  (ws://host:8001/ws)    │
└────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         Postgres          Redis           SMTP relay
    (authoritative)    (pub/sub only)    (alerts only)
              │               │
   ┌──────────┘               │
   │  Existing tables         │  channel: dashboard:events
   │  + new tables:           │
   │    system_metrics        │
   │    event_stream          │
   │    alert_events          │
   └──────────────────────────┘
              ▲
   ┌──────────┴──────────────┐
   │  Worker (existing)      │
   │  + publish_event() hook │
   └─────────────────────────┘
```

---

## Dashboard API service

**Location**: `app/api/routes/dashboard_v2.py`
**Port**: 8001 (separate process from main API on port 8000)
**Framework**: FastAPI (same dependency as main API)
**Auth**: Bearer token via `require_dashboard_auth()` from `app/api/deps.py`

### Route groups

| Route | Method | Auth required | Description |
|---|---|---|---|
| `/dashboard/health` | GET | Optional (configurable) | System health tiles |
| `/dashboard/metrics` | GET | Optional | KPI aggregates, stuck jobs, error rate |
| `/dashboard/events` | GET | Optional | Event stream (cursor-based) |
| `/dashboard/lead/{id}/trace` | GET | Optional | Full pipeline trace for a lead |
| `/dashboard/alerts` | GET | Optional | Active and recent alerts |
| `/dashboard/actions/retry` | POST | Required | Retry a failed job |
| `/dashboard/actions/cancel` | POST | Required | Cancel pending jobs for a lead |
| `/dashboard/actions/finalize` | POST | Required | Force finalize a lead |
| `/dashboard/actions/resolve` | POST | Required | Resolve an exception |
| `/dashboard/actions/ignore` | POST | Required | Ignore an exception |
| `ws://host/dashboard/ws/events` | WebSocket | Optional | Real-time event feed |

Full request/response schemas: see `spec/dashboard/07_api_contracts.md`.

---

## Metrics collector job

**Location**: `app/worker/jobs/metrics_jobs.py`
**Pattern**: Self-rescheduling, same as `nurture_scheduler.py`
**Interval**: 60 seconds
**Queue**: `default`

### Execution steps
1. Open a Postgres session.
2. Compute each metric via a targeted SQL query (no joins across more than 3 tables).
3. Write one `system_metrics` row per metric with `recorded_at = now()`.
4. Call `evaluate_alerts(session, settings)` from `app/services/alerting.py`.
5. For each new alert: write `alert_events` row; call `send_alert_email()`.
6. For each resolved alert: update `alert_events.status`; send resolve email.
7. Self-reschedule via `schedule_job()`.
8. Individual metric failures are caught, logged, and do not abort the full cycle.

---

## Event publisher

**Location**: `app/services/event_publisher.py`
**Invoked from**: `app/worker/claim.py` (claim/complete/fail), `app/worker/exceptions.py` (create_exception), `app/core/campaigns.py` (apply_campaign_switch), `app/worker/jobs/call_processing.py` (call_processed)

### Contract
```python
def publish_event(
    session,
    event_type: str,      # job_started | job_completed | job_failed |
                          # exception_created | call_processed | campaign_switched | alert_triggered
    entity_type: str,
    entity_id: str,
    contact_id: str | None,
    message: str,
    payload: dict | None = None,
) -> None:
```

### Behavior
1. Write row to `event_stream` (always — non-fatal).
2. Serialize event to JSON.
3. Publish to Redis channel `dashboard:events` via `PUBLISH` command.
4. If Redis unavailable: log warning, skip Pub/Sub. `event_stream` write is the fallback.
5. If Postgres write fails: log error, do not raise. Parent job must not fail due to event publication failure.

---

## Alerting service

**Location**: `app/services/alerting.py`

### Alert types and defaults

| Alert type | Metric | Default threshold | Severity |
|---|---|---|---|
| `queue_lag_exceeded` | `queue_lag_seconds` | 300 s | critical |
| `error_rate_spike` | `error_rate` | 0.20 (20%) | warning |
| `exception_spike` | `open_exception_count` | 10 | warning |
| `worker_offline` | `active_workers` | 0 | critical |
| `ghl_auth_failure` | exceptions of type `ghl_auth_failed` count | 1 | critical |
| `duplicate_rate_spike` | `duplicate_action_rate` | 0.05 (5%) | warning |

All thresholds are settings-driven (`ALERT_*` env vars). See `spec/dashboard/09_security_privacy.md` for env var list.

### Deduplication
- `alert_events` table tracks `(alert_type, status)`.
- Before sending: check for active row with same `alert_type` where `created_at > now() - ALERT_DEDUP_WINDOW_SECONDS`.
- If found: update `last_seen_at`; skip send.
- If not found: insert new row; send email.

---

## WebSocket gateway

**Location**: inline in `app/api/routes/dashboard_v2.py` using FastAPI `WebSocket`
**Redis dependency**: `redis.asyncio` from `redis>=4.5` (built-in async support; do NOT use the deprecated `aioredis` package)

### Connection lifecycle
1. Client connects to `ws://localhost:8001/ws`.
2. Server subscribes to Redis channel `dashboard:events`.
3. On message: forward JSON to WebSocket client.
4. On Redis disconnect: send `{"type": "error", "code": "realtime_unavailable", "fallback_url": "/dashboard/events"}` and close.
5. Client catches close event and switches to 5-second polling of `GET /dashboard/events`.

---

## Frontend structure (Next.js)

No UI code is generated by this spec. Structure is defined in `spec/dashboard/04_breakdown_plan.md` Phase 6.

**Key constraints**:
- All data fetching goes through `lib/api.ts` typed wrappers pointing to `DASHBOARD_API_URL=http://localhost:8001`. No direct `fetch` calls in component files.
- All real-time logic lives in `lib/websocket.ts` using `WS_URL=ws://localhost:8001/ws`. Components consume a `useActivityFeed()` hook.
- Charts use Recharts. No chart library with a paid tier.
- Server-side rendering is used for initial page load of trace and exception views (faster first paint with Postgres data).
- All timestamps rendered in the browser use the `Intl.DateTimeFormat` API with the user's local timezone; UTC is shown as secondary.
- CORS: dashboard API accepts requests from `ALLOW_ORIGINS=http://localhost:3000` only.

---

## Deployment topology

```
docker-compose.yml additions:

  dashboard-api:
    build: .
    command: uvicorn app.api.dashboard_main:app --port 8001
    ports: ["8001:8001"]
    depends_on: [db, redis]
    environment: [same .env as api]

  dashboard-ui:
    build: ./dashboard-ui
    command: node server.js
    ports: ["3000:3000"]
    depends_on: [dashboard-api]
```

The `metrics_jobs` scheduler is started by the existing worker process alongside `nurture_scheduler`. No additional worker process is required.

---

## Key trade-offs
- Separate port (8001) for dashboard API avoids coupling dashboard traffic and operator action auth to the main Synthflow webhook ingestion API (port 8000). A misconfigured dashboard auth header cannot accidentally affect webhook processing.
- Self-rescheduling metrics job (versus cron) keeps the implementation consistent with the existing `nurture_scheduler` pattern and avoids introducing a cron dependency.
- `event_stream` table as fallback ensures real-time events are never lost when Redis is temporarily unavailable, at the cost of a write per event. Volume is bounded: events are generated only on job state transitions, not on every DB query.
- SMTP over a webhook-based alerting tool eliminates an external dependency and a paid tier, at the cost of lower delivery SLA. Acceptable given the operational model.
