# spec/dashboard/05_eval_plan.md

## Evaluation strategy
Each phase has a defined eval suite that must pass before the phase is considered complete. "Looks reasonable" is not a valid evaluation result.

---

## Phase 1 — API foundation evals

### EVAL-API-01: Health metric accuracy
**Type**: Unit
**Setup**: Insert 3 `scheduled_jobs` rows: one `pending` with `run_at = now() - 15 min`, one `running` with valid lease, one `completed`.
**Expected**:
- `queue_lag_seconds >= 900`
- `active_workers = 1`
- `stuck_job_count >= 1`

**Negative case**: All jobs `completed` → `queue_lag_seconds = 0`, `active_workers = 0`.

### EVAL-API-02: KPI arithmetic
**Type**: Unit
**Setup**: Insert 10 `call_events`: 4 `completed`, 5 voicemail-status, 1 `failed`. All in last 24h, campaign = `New Lead`.
**Expected**:
- `pickup_rate = 0.40`
- `voicemail_rate = 0.50`

**Negative case**: 0 calls in range → both rates return `null`, not `0.0` (division by zero guard).

### EVAL-API-03: Duplicate action prevention
**Type**: Unit + integration
**Setup**: One open exception. Submit retry. Submit retry again before first job completes.
**Expected**: Second request returns HTTP 409. `scheduled_jobs` has exactly one new row. `audit_log` has exactly one new row.

### EVAL-API-04: Auth enforcement
**Type**: Unit
**Setup**: Call `POST /dashboard/actions/retry` without Authorization header.
**Expected**: HTTP 401.

---

## Phase 2 — Data model evals

### EVAL-DB-01: Migration non-regression
**Type**: Integration
**Setup**: Run `alembic upgrade head` against a test database with production-schema data.
**Expected**: All existing Streamlit dashboard SQL queries execute without error. No index drops. No column renames on existing tables.

### EVAL-DB-02: `system_metrics` write and read
**Type**: Unit
**Setup**: Call `collect_metrics_job` with mock session.
**Expected**: One row inserted per metric type. Querying `system_metrics ORDER BY recorded_at DESC LIMIT 1` per metric name returns the just-inserted value.

---

## Phase 3 — Real-time evals

### EVAL-RT-01: Event delivery latency
**Type**: Integration
**Setup**: Connect WebSocket client. Trigger `complete_job()` in test worker.
**Expected**: Event appears in client within 2 seconds. Measured by timestamp diff between `scheduled_jobs.updated_at` and WebSocket message receipt.

### EVAL-RT-02: Redis failure fallback
**Type**: Integration
**Setup**: Stop Redis. Trigger a `job_completed` event.
**Expected**: `event_stream` row is written. Polling `GET /dashboard/events` returns the event. No unhandled exception in the worker log.

### EVAL-RT-03: Cursor-based dedup
**Type**: Unit
**Setup**: Insert 5 events at T. Client polls with `since=T+1`. Then inserts 3 more events at T+2. Client polls again with `since=T+1`.
**Expected**: First poll returns 0 events (all older than cursor). After second insert, poll returns exactly 3 events.

---

## Phase 4 — Alerting evals

### EVAL-AL-01: Alert fires on threshold breach
**Type**: Unit
**Setup**: Insert `system_metrics` row with `metric_name = 'queue_lag_seconds'`, `value = 400`. `ALERT_QUEUE_LAG_THRESHOLD_SECONDS = 300`.
**Expected**: `evaluate_alerts()` returns one `AlertEvent` with `severity = 'critical'`. `send_alert_email()` is called once. `alert_events` row inserted with `status = 'active'`.

### EVAL-AL-02: Deduplication suppresses second send
**Type**: Unit
**Setup**: Active `alert_events` row for `queue_lag` within dedup window. Insert another metric breach.
**Expected**: `send_alert_email()` not called. `alert_events` row `last_seen_at` updated.

### EVAL-AL-03: Resolve email sent
**Type**: Unit
**Setup**: Active `alert_events` row for `queue_lag`. Metric drops below threshold.
**Expected**: `status` set to `resolved`. `send_alert_email()` called with resolve template. Resolve timestamp recorded.

### EVAL-AL-04: SMTP disabled mode
**Type**: Unit
**Setup**: `SMTP_ENABLED=false`.
**Expected**: `send_alert_email()` logs to stdout. No SMTP connection attempted. No exception raised.

---

## Phase 5 — Pipeline trace evals

### EVAL-TR-01: Happy path — completed call
**Type**: Unit
**Setup**: Insert a full set of `scheduled_jobs` rows for one lead covering all job types, all `completed`. Insert matching `call_events`, `classification_results`, `task_events`.
**Expected**: `build_pipeline_trace()` returns steps in chronological order. All steps have `status = 'completed'`. No `exception_id`. No `is_shadow`.

### EVAL-TR-02: Failed step linked to exception
**Type**: Unit
**Setup**: `create_crm_task` job with `status = 'failed'`. Matching `exceptions` row with same `entity_id`.
**Expected**: Trace step for `create_crm_task` has `status = 'failed'` and `exception_id` matches.

### EVAL-TR-03: Shadow step identified
**Type**: Unit
**Setup**: `send_sms` job `completed`. `outbound_messages` row with `status = 'shadow'`. `shadow_actions` row with `action_type = 'sms'`.
**Expected**: Trace step `is_shadow = true`. `shadow_payload.message_body` non-empty.

### EVAL-TR-04: Lead not found
**Type**: Unit
**Setup**: Request trace for a `contact_id` not in any table.
**Expected**: HTTP 404. No stack trace in response.

---

## Regression eval suite (run after every phase)

### EVAL-REG-01: Streamlit dashboard loads
- `streamlit run execution/dashboard.py --server.headless true` starts without error.
- All 11 section queries execute against the migrated schema in < 5 seconds.

### EVAL-REG-02: Worker jobs unaffected
- Run `python -m pytest tests/unit/ -q` → 886+ passing.

### EVAL-REG-03: No schema lock conflicts
- Apply migration in a transaction. Confirm no `AccessExclusiveLock` held for more than 1 second on `call_events`, `lead_state`, or `scheduled_jobs`.

### EVAL-REG-04: SQL correctness without Postgres (production regression)
`TestGetRecentCallsSql` in `tests/unit/test_dashboard_v2.py` intercepts `session.execute()`, captures the SQL string, and asserts:
- No `E'` prefix before regex literals (would cause backslash stripping in PG).
- `{7,}` present and `(7,)` absent (f-string brace escaping correct).
- `^\+?` anchor intact (not collapsed to `^+`).
- Named params use `:from_dt` / `:to_dt` / `:limit` style (not `%(x)s`).

Run with: `python -m pytest tests/unit/test_dashboard_v2.py::TestGetRecentCallsSql -v`

These four tests are intentionally runnable without a live Postgres — they caught the production `InvalidRegularExpression` bug (2026-04-18) that unit mocks would have missed.

---

## Performance eval

### EVAL-PERF-01: Health endpoint under load
**Setup**: Database with 1000 leads, 10 000 `call_events`, 5000 `scheduled_jobs`.
**Expected**: `GET /dashboard/health` responds in < 500 ms p95.

### EVAL-PERF-02: Pipeline trace under load
**Setup**: Lead with 50 `scheduled_jobs` rows, 10 `call_events`.
**Expected**: `GET /dashboard/lead/{id}/trace` responds in < 3 s p95.

### EVAL-PERF-03: Metrics endpoint under load
**Same setup as EVAL-PERF-01.**
**Expected**: `GET /dashboard/metrics` responds in < 1 s p95.
