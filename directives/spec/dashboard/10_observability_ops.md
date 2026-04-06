# spec/dashboard/10_observability_ops.md

## Logs

### Dashboard API logs
- Request received: method, path, operator_id (if present), response status, duration_ms
- Operator action taken: action type, exception_id or contact_id, operator_id, result (ok / 409 / 404)
- Auth failure: path, reason (missing token / invalid token)
- Metrics query: metric names computed, duration_ms
- Alert evaluation: alerts checked, alerts fired, alerts resolved

### Metrics collector logs
- Cycle start: timestamp, cycle_id
- Per metric: name, computed value, duration_ms
- Alert thresholds checked: metric name, current value, threshold, fired (true/false)
- Email send attempt: alert_type, recipient, success/failure
- Cycle complete: total duration_ms, metrics written, errors (if any)

### Event publisher logs (added to existing worker jobs)
- Event published: event_type, entity_id, contact_id, redis_published (true/false)
- Redis publish failure: error message (non-fatal — parent job continues)
- event_stream write failure: error message (non-fatal)

### What is not logged
- Full transcript content (even at DEBUG level in production)
- GHL API response bodies (contact PII)
- SMTP credentials
- Bearer tokens or SECRET_KEY values

---

## Metrics (collected by metrics_jobs.py)

All metrics are written to `system_metrics` every 60 seconds. Labels allow per-campaign segmentation where noted.

| Metric name | Derivation | Labels |
|---|---|---|
| `queue_lag_seconds` | `EXTRACT(EPOCH FROM now() - MIN(run_at))` for pending past-due jobs | none |
| `active_workers` | distinct `claimed_by` where running + lease valid | none |
| `open_exception_count` | `exceptions WHERE status='open'` | none |
| `stuck_job_count` | pending jobs with `run_at < now() - 10m` | none |
| `expired_lease_count` | running jobs with `lease_expires_at < now()` | none |
| `jobs_completed_5m` | completed jobs in last 5 min | none |
| `jobs_failed_5m` | failed jobs in last 5 min | none |
| `error_rate` | `jobs_failed_5m / jobs_completed_5m` | none |
| `pickup_rate` | completed / total calls last 24h | `{"campaign": "..."}` |
| `voicemail_rate` | voicemail-status calls / total calls last 24h | `{"campaign": "..."}` |
| `blank_transcript_rate` | calls with empty transcript / total calls last 24h | none |
| `ghl_task_success_rate` | `task_events WHERE status='created'` / all `create_crm_task` jobs completed last 24h | none |
| `ghl_vm_update_success_rate` | `update_ghl_after_vm_message` completed / (completed + failed) last 24h | none |
| `duplicate_action_rate` | `call_events` with duplicate `dedupe_key` count / total | none |

---

## Alerts

### Alert definitions

| Alert type | Trigger condition | Severity | Default threshold |
|---|---|---|---|
| `queue_lag_exceeded` | `queue_lag_seconds > ALERT_QUEUE_LAG_THRESHOLD_SECONDS` | critical | 300 s |
| `worker_offline` | `active_workers = 0` | critical | N/A |
| `error_rate_spike` | `error_rate > ALERT_ERROR_RATE_THRESHOLD` | warning | 0.20 |
| `exception_spike` | `open_exception_count > ALERT_EXCEPTION_SPIKE_THRESHOLD` | warning | 10 |
| `ghl_auth_failure` | `exceptions WHERE type='ghl_auth_failed' AND created_at > now() - 5m` count > 0 | critical | 1 |
| `duplicate_rate_spike` | `duplicate_action_rate > 0.05` | warning | 0.05 |

### Alert email structure

**Subject**: `[{SEVERITY}] Cora Recap Engine — {alert_type}`
Example: `[CRITICAL] Cora Recap Engine — queue_lag_exceeded`

**Body**:
```
Alert: {alert_type}
Severity: {severity}
Time: {timestamp UTC}

Current value: {current_value}
Threshold: {threshold}

Action required: {human-readable guidance per alert type}

View dashboard: http://{DASHBOARD_HOST}:{DASHBOARD_PORT}/health
```

**Resolve email subject**: `[RESOLVED] Cora Recap Engine — {alert_type}`

### Per-alert guidance text (written into templates)
- `queue_lag_exceeded`: "Check worker health in the dashboard. Confirm Redis is reachable. Review stuck jobs."
- `worker_offline`: "No workers are processing jobs. Restart the worker service immediately."
- `error_rate_spike`: "More than 20% of recent jobs are failing. Check Exceptions section for root cause."
- `exception_spike`: "More than 10 exceptions are open. Review and triage in the Exceptions section."
- `ghl_auth_failure`: "GHL API authentication is failing. Check GHL_API_KEY and GHL_LOCATION_ID in environment."
- `duplicate_rate_spike`: "Duplicate dedupe key rate is elevated. Check for replay attacks or webhook retries."

---

## Operational dashboards

### Health section (primary ops view)
- Queue lag tile: current seconds, 24h sparkline from `system_metrics`
- Worker count tile: current active workers
- Error rate tile: current rate + 1h trend
- Open exceptions tile: count with breakdown by severity

### Queue section
- Stuck jobs table: job_id, job_type, contact_id, lag_seconds, actions (cancel)
- Expired leases table: job_id, worker_id, age

### Exceptions section
- Grouped by `type`: count per type, last seen
- Flat list: filterable by severity, type, date
- Per exception: context_json expanded, action buttons (retry, cancel, finalize, resolve, ignore)

### AI performance section
- Blank transcript rate: current + 7d trend
- Intent distribution: bar chart from `call_events.detected_intent`
- Consent distribution: pie chart from `summary_results.summary_consent`
- Prompt family latency: derived from job timing

### CRM health section
- GHL task success rate: current + 7d trend
- VM update success rate
- Shadow GHL write count (active while shadow mode is on)
- GHL auth failure exceptions

---

## Runbook cross-reference
All operational procedures for the dashboard system itself are in `spec/dashboard/11_runbook.md`.
