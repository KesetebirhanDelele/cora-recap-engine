# spec/dashboard/04_breakdown_plan.md

## Decomposition strategy
Each phase is independently deployable and testable. Later phases depend on earlier phases only through stable API contracts. The Streamlit dashboard remains operational throughout all phases.

---

## Phase 1 — Dashboard API foundation
**Artifact**: `app/api/routes/dashboard_v2.py` + `app/services/dashboard_metrics.py`
**Dependencies**: Existing Postgres schema, existing `app/services/dashboard.py` for operator actions.

### Chunks

#### 1A — Health endpoint
- Implement `GET /dashboard/health`
- Queries: `scheduled_jobs` for queue lag and worker health; `exceptions` for open count; derived throughput from job completions in last 5 min
- Returns typed JSON response (pydantic schema)
- Unit tests: mock Postgres responses; verify metric calculation logic
- Acceptance: AC-SH-01, AC-SH-02

#### 1B — Metrics endpoint
- Implement `GET /dashboard/metrics`
- Queries: stuck jobs, expired leases, error rate, KPI aggregates (pickup rate, voicemail rate)
- Accepts query params: `campaign`, `from_date`, `to_date`
- Unit tests: verify KPI arithmetic; edge case when no calls in range
- Acceptance: AC-SH-03, AC-QM-01, AC-QM-02, AC-KPI-01

#### 1C — Operator actions (reuse existing service)
- Wire `POST /dashboard/actions/retry`, `/cancel`, `/finalize` to existing `app/services/dashboard.py` functions
- Add `operator_id` extraction from Bearer token
- Add HTTP 409 guard for duplicate actions (check pending jobs before enqueuing)
- Unit tests: duplicate action returns 409; audit log row written; shadow mode respected
- Acceptance: AC-EX-01, AC-EX-02, AC-EX-03, AC-EX-04, AC-NR-03

#### 1D — Auth middleware
- Reuse `require_dashboard_auth()` from `app/api/deps.py` for all operator action routes
- Read-only routes (`/health`, `/metrics`, `/events`, `/lead/*/trace`) optionally auth-gated by `DASHBOARD_READ_AUTH_REQUIRED` setting (default: false for dev, true for prod)
- Unit tests: unauthenticated POST returns 401

---

## Phase 2 — Data model additions
**Artifact**: `migrations/versions/0011_dashboard_tables.py`
**Dependencies**: Phase 1 (API must know the table schemas before querying them)

### Chunks

#### 2A — `system_metrics` table
- Schema: see `spec/dashboard/08_data_model.md`
- Populated by a new background job `collect_metrics_job` running every 60 seconds
- Index on `(metric_name, recorded_at)` for time-series queries

#### 2B — `event_stream` table
- Schema: see `spec/dashboard/08_data_model.md`
- Written by worker jobs via a new `publish_event()` helper (non-fatal; exceptions logged but do not fail the parent job)
- Index on `(created_at DESC)` for cursor-based pagination

#### 2C — `alert_events` table
- Schema: see `spec/dashboard/08_data_model.md`
- Written by the alerting scheduler
- Index on `(alert_type, status, created_at)`

#### 2D — Migration safety check
- Confirm all new tables have no FK constraints on existing tables (avoid lock contention)
- Confirm `execution/dashboard.py` imports and queries are unaffected by running the migration
- Test: run existing Streamlit unit-level queries against migrated schema

---

## Phase 3 — Real-time events
**Artifact**: `app/services/event_publisher.py`, `app/worker/jobs/metrics_jobs.py`
**Dependencies**: Phase 2 (event_stream table must exist)

### Chunks

#### 3A — Event publisher helper
- `publish_event(event_type, entity_type, entity_id, contact_id, message, payload)` — writes to `event_stream` table and publishes to Redis channel `dashboard:events`
- Non-fatal: if Redis is down, only writes to table
- Call sites: `process_call_event` (call_processed), `claim_job` (job_started), `complete_job` (job_completed), `fail_job` (job_failed), `create_exception` (exception_created), `apply_campaign_switch` (campaign_switched)

#### 3B — Events API endpoint
- `GET /dashboard/events?since=<iso_ts>&limit=100`
- Queries `event_stream` with cursor-based pagination
- Acceptance: AC-LF-02, AC-LF-03

#### 3C — WebSocket gateway
- FastAPI WebSocket endpoint at `ws://host/dashboard/ws/events`
- Subscribes to Redis channel `dashboard:events`; forwards messages to connected clients
- On Redis failure: returns `{"error": "realtime_unavailable", "fallback": "poll /dashboard/events"}`
- Acceptance: AC-LF-01

#### 3D — Metrics collector job
- `collect_metrics_job` runs every 60 seconds (self-rescheduling, same pattern as nurture_scheduler)
- Writes rows to `system_metrics` for: queue_lag, error_rate, active_workers, pickup_rate, voicemail_rate
- Also runs alert threshold checks (feeds Phase 4)

---

## Phase 4 — Alerting system
**Artifact**: `app/services/alerting.py`, email templates
**Dependencies**: Phase 3 (collect_metrics_job produces metric values)

### Chunks

#### 4A — Alert threshold evaluator
- `evaluate_alerts(session, settings)` — reads latest `system_metrics` rows; compares against thresholds from settings
- Returns list of `AlertEvent` objects (type, severity, current_value, threshold)
- Pure function — testable without I/O

#### 4B — Alert deduplication and state management
- Before sending: check `alert_events` for active (unresolved) alert of same type within `ALERT_DEDUP_WINDOW_SECONDS`
- If active alert exists: skip send; update `last_seen_at`
- If alert resolves: set `status = 'resolved'`; send resolve email
- Acceptance: AC-AL-02, AC-AL-03

#### 4C — SMTP email sender
- `send_alert_email(alert, settings)` — renders template; sends via `smtplib` (standard library)
- When `SMTP_ENABLED=false`: logs to stdout instead
- Templates: one per alert type (see `spec/dashboard/11_runbook.md` for content)
- Acceptance: AC-AL-01

---

## Phase 5 — Pipeline trace endpoint
**Artifact**: `app/services/pipeline_trace.py`
**Dependencies**: Phase 1 (API routing exists)

### Chunks

#### 5A — Trace builder
- `build_pipeline_trace(session, contact_id)` — joins `scheduled_jobs`, `call_events`, `exceptions`, `task_events`, `outbound_messages`, `shadow_actions`, `classification_results`
- Returns ordered list of `TraceStep` objects
- Each step maps one `scheduled_jobs` row to its outcome
- Shadow steps detected by `outbound_messages.status = 'shadow'` or `shadow_actions` presence
- Acceptance: AC-PT-01, AC-PT-02, AC-PT-03

#### 5B — Trace API endpoint
- `GET /dashboard/lead/{contact_id}/trace`
- Accepts `contact_id` path param; also accepts phone via `?phone=+1XXXXXXXXXX`
- Returns full `PipelineTrace` schema
- Unit tests: happy path, failed step, shadow step, lead not found (404)

---

## Phase 6 — Next.js frontend structure
**Artifact**: `dashboard-ui/` directory (structure only — no UI code generated by spec)
**Dependencies**: All API phases complete and stable

### Structure
```
dashboard-ui/
  app/
    page.tsx               ← home (health tiles + live feed)
    health/page.tsx
    campaigns/page.tsx
    ai-performance/page.tsx
    crm-health/page.tsx
    kpis/page.tsx
    exceptions/page.tsx
    queue/page.tsx
    lead/[id]/page.tsx     ← pipeline trace + journey
    alerts/page.tsx
    settings/page.tsx
  components/
    HealthTiles.tsx
    ActivityFeed.tsx
    PipelineTrace.tsx
    ExceptionQueue.tsx
    CampaignFunnel.tsx
    QueueTable.tsx
    KpiCards.tsx
    AlertBanner.tsx
  lib/
    api.ts                 ← typed fetch wrappers for all dashboard API endpoints
    websocket.ts           ← WebSocket client with polling fallback
  types/
    index.ts               ← TypeScript types matching API response schemas
```

---

## Phase ordering and dependencies

```
Phase 1 (API) → Phase 2 (DB) → Phase 3 (Events) → Phase 4 (Alerting)
                                                  → Phase 5 (Trace)
                                                  → Phase 6 (Frontend)
```

Phase 6 can begin structure and type scaffolding in parallel with Phase 3 once Phase 1 API contracts are stable.
