# spec/dashboard/02_acceptance_criteria.md

## System health

### AC-SH-01 — Queue lag visible and accurate
- Given: one or more `scheduled_jobs` rows with `status = 'pending'` and `run_at < now()`
- When: `GET /dashboard/health` is called
- Then: `queue_lag_seconds` equals `EXTRACT(EPOCH FROM now() - MIN(run_at))` over matching rows
- Edge case: no pending past-due rows → `queue_lag_seconds = 0`

### AC-SH-02 — Worker health count is correct
- Given: workers are running with valid lease expiry
- When: `GET /dashboard/health` is called
- Then: `active_workers` equals the count of distinct `claimed_by` values in `scheduled_jobs WHERE status = 'running' AND lease_expires_at > now()`
- Edge case: all leases expired → `active_workers = 0`; dashboard shows "no active workers" warning

### AC-SH-03 — Error rate reflects recent exceptions
- Given: N exceptions created in the last 5 minutes; M jobs completed in the same window
- When: `GET /dashboard/metrics` is called
- Then: `error_rate = N / M` when M > 0; `null` when M = 0
- Edge case: error rate spike (N/M > configured threshold) → alert trigger fires (see AC-AL-01)

## Live activity feed

### AC-LF-01 — Events delivered within 2 seconds
- Given: a `job_completed` event is published to Redis channel `dashboard:events`
- When: the frontend is connected via WebSocket
- Then: the event appears in the feed within 2 seconds of the Postgres `updated_at` write

### AC-LF-02 — No duplicate events on reconnect
- Given: a client disconnects and reconnects, providing `since=<last_event_ts>`
- When: `GET /dashboard/events?since=<ts>` is polled
- Then: only events with `created_at > since` are returned; no duplicates

### AC-LF-03 — Polling fallback when Redis unavailable
- Given: Redis connection fails
- When: frontend polls `GET /dashboard/events` every 5 seconds
- Then: events are returned from `event_stream` table; feed continues without error

## Pipeline trace

### AC-PT-01 — Full lifecycle rendered for a completed-call lead
- Given: a lead with a completed call, AI analysis, CRM task, and at least one voicemail follow-up
- When: `GET /dashboard/lead/{contact_id}/trace` is called
- Then: response contains ordered steps covering all job_types that ran, each with `started_at`, `completed_at`, `duration_ms`, and `status`

### AC-PT-02 — Failed step is flagged
- Given: a lead whose `create_crm_task` job failed and has an open exception
- When: the trace is fetched
- Then: the `create_crm_task` step has `status = 'failed'` and `exception_id` is non-null and references the correct exception row

### AC-PT-03 — Shadow steps labeled
- Given: shadow mode is on; `send_sms_job` produced a shadow row in `outbound_messages` and `shadow_actions`
- When: the trace is fetched
- Then: the SMS step has `is_shadow = true` and `shadow_payload` contains the generated message body

## Exception management

### AC-EX-01 — Retry now enqueues the job
- Given: an open exception with a failed `create_crm_task` job
- When: `POST /dashboard/actions/retry` is called with `exception_id`
- Then: a new `scheduled_jobs` row is created with `status = 'pending'` and `run_at = now()`; the exception status is set to `resolved`; an `audit_log` row is written

### AC-EX-02 — Force finalize writes correct fields
- Given: a lead stuck at tier 1 with an open exception
- When: `POST /dashboard/actions/finalize` is called
- Then: `lead_state.status = 'closed'`; all pending jobs for the lead are cancelled; an `audit_log` row is written with `action = 'force_finalize'`; no GHL write is triggered directly from the dashboard API

### AC-EX-03 — Duplicate action prevented
- Given: operator submits retry for exception ID X
- When: a second retry is submitted for the same exception ID before the first job completes
- Then: API returns HTTP 409 with message "action already pending for this exception"; no duplicate `scheduled_jobs` row is created

### AC-EX-04 — All actions audit-logged
- Given: any of (retry, cancel, finalize, resolve, ignore) is submitted
- When: the action completes
- Then: `audit_log` contains one new row with `entity_type = 'exception'`, `entity_id = exception_id`, correct `action`, and `operator_id` from the authenticated session

## Queue monitoring

### AC-QM-01 — Stuck jobs detected
- Given: a `scheduled_jobs` row with `status = 'pending'` and `run_at < now() - interval '10 minutes'`
- When: `GET /dashboard/metrics` is called
- Then: `stuck_job_count` is non-zero and the specific job IDs appear in the stuck jobs list

### AC-QM-02 — Expired lease detected
- Given: a `scheduled_jobs` row with `status = 'running'` and `lease_expires_at < now()`
- When: `GET /dashboard/metrics` is called
- Then: the job appears in the expired lease list with `worker_id` and age

### AC-QM-03 — Cancel stuck job from Queue Health UI
- Given: the Queue Health page shows a stuck job with a non-null `contact_id`
- When: the operator clicks **Cancel jobs**
- Then: `POST /dashboard/actions/cancel` is called with that `contact_id`; all `pending` jobs for the contact transition to `cancelled`; the page re-fetches and the contact's jobs no longer appear in the stuck list; one `audit_log` row is written
- Edge case: stuck job with `contact_id = null` → no Cancel button rendered; operator must act via the Exceptions Monitor or direct DB query
- Edge case: expired lease rows never show a Cancel button (no `contact_id` in query; auto-recovery handles them)

## Campaign and AI metrics

### AC-KPI-01 — Pickup rate correct
- Given: 100 calls in range, 40 with `status = 'completed'`
- When: `GET /dashboard/metrics?campaign=New+Lead&from=...&to=...` is called
- Then: `pickup_rate = 0.40`

### AC-KPI-02 — AI blank transcript rate correct
- Given: 50 `call_events` rows with `transcript = ''` or `transcript IS NULL` in range
- When: AI performance metrics are fetched
- Then: `blank_transcript_rate` equals 50 / total calls in range

## Alerting

### AC-AL-01 — Alert email sent on threshold breach
- Given: `queue_lag_seconds` exceeds `ALERT_QUEUE_LAG_THRESHOLD_SECONDS` (default 300)
- When: the metric check runs (every 60 seconds)
- Then: one alert email is sent to `ALERT_EMAIL_TO` with subject "CRITICAL: Queue lag exceeded" and body containing current value, threshold, and timestamp

### AC-AL-02 — Alert deduplication prevents spam
- Given: an alert email was sent for queue lag at T
- When: queue lag remains above threshold at T+30s
- Then: no second email is sent until `ALERT_DEDUP_WINDOW_SECONDS` (default 3600) has elapsed since the first send, OR the alert resolves and re-triggers

### AC-AL-03 — Alert resolve email sent
- Given: a critical alert was active; the metric drops below threshold
- When: the next metric check runs
- Then: a resolve email is sent noting the metric returned to normal

### AC-AL-04 — Acknowledge alert via UI
- Given: an `alert_events` row with `status = 'active'` is visible on the Active tab
- When: the operator clicks **Acknowledge**
- Then: `POST /dashboard/actions/acknowledge-alert` is called with the alert's `id`; the alert row is removed from the Active tab immediately (optimistic); switching to the Acknowledged tab shows the alert with its original message and severity
- Backend: `alert_events.status = 'acknowledged'` and `resolved_at = now()`; one `audit_log` row with `action = 'acknowledge_alert'`
- Edge case: double-click or concurrent acknowledge → second request returns HTTP 409; UI shows the error inline; the alert remains visible
- Edge case: no Dashboard Token set → request returns HTTP 403; error is displayed inline on the alert row

## Existing system non-regression

### AC-NR-01 — Streamlit dashboard unaffected
- Given: the new system is deployed
- When: `streamlit run execution/dashboard.py` is run
- Then: all 11 sections render correctly; no import errors; no schema conflicts

### AC-NR-02 — No new tables break existing queries
- Given: new tables (`system_metrics`, `event_stream`, `alert_events`) are migrated
- When: existing Streamlit queries run
- Then: no query errors; existing indexes are not affected

### AC-NR-03 — Operator actions remain idempotent
- Given: the same `retry` action is submitted twice within 1 second (race condition)
- When: both requests are processed
- Then: exactly one new `scheduled_jobs` row is created; exactly one `audit_log` row is written
