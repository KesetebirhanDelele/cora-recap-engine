# spec/dashboard/08_data_model.md

## New tables

All new tables are created in migration `0011_dashboard_tables`. No existing table is modified.

---

## system_metrics

Aggregated time-series metric snapshots. One row per metric per collection cycle.

```sql
CREATE TABLE system_metrics (
    id              VARCHAR(36)     PRIMARY KEY,
    metric_name     VARCHAR(100)    NOT NULL,
    value           DOUBLE PRECISION NOT NULL,
    labels          JSONB           NOT NULL DEFAULT '{}',
    recorded_at     TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_system_metrics_name_ts
    ON system_metrics (metric_name, recorded_at DESC);
```

**`metric_name` values** (exhaustive list; no free-form strings):
- `queue_lag_seconds`
- `active_workers`
- `open_exception_count`
- `stuck_job_count`
- `expired_lease_count`
- `jobs_completed_5m`
- `jobs_failed_5m`
- `error_rate`
- `pickup_rate`
- `voicemail_rate`
- `blank_transcript_rate`
- `ghl_task_success_rate`
- `ghl_vm_update_success_rate`
- `duplicate_action_rate`

**`labels`**: JSONB for optional segmentation. Example: `{"campaign": "New Lead"}`. Empty object `{}` when no segmentation.

**Retention**: rows older than 30 days are eligible for deletion. A cleanup job running weekly truncates old rows. This table is not used for long-term KPI reporting — `call_events` and `lead_state` remain the sources of truth for historical KPIs.

---

## event_stream

Ordered stream of system events for the activity feed and polling fallback.

```sql
CREATE TABLE event_stream (
    id              VARCHAR(36)     PRIMARY KEY,
    event_type      VARCHAR(50)     NOT NULL,
    entity_type     VARCHAR(50),
    entity_id       VARCHAR(255),
    contact_id      VARCHAR(255),
    message         TEXT            NOT NULL,
    payload         JSONB           NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_event_stream_created_at
    ON event_stream (created_at DESC);

CREATE INDEX idx_event_stream_contact_id
    ON event_stream (contact_id, created_at DESC);
```

**`event_type` values**:
- `job_started`
- `job_completed`
- `job_failed`
- `exception_created`
- `call_processed`
- `campaign_switched`
- `alert_triggered`

**Retention**: rows older than 7 days are deleted on the weekly cleanup cycle. The event stream is a rolling window — long-term history is in `audit_log` and `call_events`.

**`payload`**: event-specific context. For `job_completed`: `{"job_type": "send_sms", "duration_ms": 1200}`. For `call_processed`: `{"call_status": "voicemail", "campaign_name": "Cold Lead"}`. No PII, no transcript content, no GHL API keys.

---

## alert_events

Active and historical alert state. One row per alert instance.

```sql
CREATE TABLE alert_events (
    id              VARCHAR(36)     PRIMARY KEY,
    alert_type      VARCHAR(100)    NOT NULL,
    severity        VARCHAR(20)     NOT NULL,  -- critical | warning
    status          VARCHAR(20)     NOT NULL DEFAULT 'active',  -- active | resolved | acknowledged
    current_value   DOUBLE PRECISION,
    threshold       DOUBLE PRECISION,
    message         TEXT            NOT NULL,
    email_sent_at   TIMESTAMPTZ,
    last_seen_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    acknowledged_by VARCHAR(255),
    acknowledged_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_alert_events_type_status
    ON alert_events (alert_type, status, created_at DESC);
```

**`alert_type` values** (matches alerting service definitions):
- `queue_lag_exceeded`
- `error_rate_spike`
- `exception_spike`
- `worker_offline`
- `ghl_auth_failure`
- `duplicate_rate_spike`

**Deduplication key**: `(alert_type, status='active')`. Before inserting a new row, check for an existing active row of the same type within `ALERT_DEDUP_WINDOW_SECONDS`. If found, update `last_seen_at` only.

---

## Existing tables used (read-only from dashboard API)

The following tables are queried by the dashboard API but never written to from the dashboard layer (write side is the worker only):

| Table | Dashboard usage |
|---|---|
| `lead_state` | Campaign filter, status, tier distribution, DNC counts |
| `call_events` | Call volume, pickup/voicemail rates, transcript quality, intent distribution |
| `scheduled_jobs` | Queue lag, stuck jobs, expired leases, worker health, pipeline trace steps |
| `exceptions` | Open exception count, exception triage, root cause grouping |
| `classification_results` | AI performance metrics (success rate, prompt family latency) |
| `summary_results` | Consent distribution, summary writeback rate |
| `task_events` | GHL task creation success rate |
| `outbound_messages` | Message content (including shadow), delivery status |
| `shadow_actions` | Shadow call/SMS/email/GHL intercept counts and payloads |
| `audit_log` | Operator action history (read); dashboard API also writes here for its own actions |

---

## audit_log writes from dashboard API

The dashboard API writes `audit_log` rows using the same schema as the existing worker writes. No new columns are needed.

**`entity_type` values used by dashboard**: `exception`, `lead`, `scheduled_job`

**`action` values used by dashboard**: `retry_now`, `retry_delay`, `cancel_future_jobs`, `force_finalize`, `resolve`, `ignore`, `alert_acknowledged`

**`operator_id`**: extracted from Bearer token claim. For unauthenticated read-only requests, operator_id is not applicable and no audit row is written.

---

## Migration checklist for 0011_dashboard_tables

- [ ] `system_metrics` table created with index
- [ ] `event_stream` table created with indexes
- [ ] `alert_events` table created with index
- [ ] No FK constraints on existing tables
- [ ] No changes to existing columns, indexes, or constraints
- [ ] Alembic downgrade function drops only the three new tables
- [ ] Test: `execution/dashboard.py` queries succeed after migration
- [ ] Test: worker jobs succeed after migration (no import or table-name conflicts)
