# spec/dashboard/11_runbook.md

## Starting the dashboard system

### Development

```bash
# 1. Start the dashboard API (separate from main API on port 8000)
uvicorn app.api.dashboard_main:app --port 8001 --reload

# 2. Start the Next.js frontend
cd dashboard-ui
npm install
npm run dev          # runs on http://localhost:3000

# 3. Confirm existing Streamlit dashboard still works (runs independently)
streamlit run execution/dashboard.py

# 4. Metrics collector starts automatically with the worker process
# It self-schedules on first worker startup via ensure_scheduled() pattern
```

### Environment variables required (development)
```
SECRET_KEY=your-dev-secret
DATABASE_URL=postgresql+psycopg2://postgres:...@localhost:5433/cora
REDIS_URL=redis://localhost:6379/0
APP_ENV=development
DASHBOARD_READ_AUTH_REQUIRED=false
ALLOW_ORIGINS=http://localhost:3000
DASHBOARD_API_URL=http://localhost:8001
FRONTEND_URL=http://localhost:3000
WS_URL=ws://localhost:8001/ws

# Gmail SMTP — set SMTP_ENABLED=false to suppress real sends locally
SMTP_ENABLED=false
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=<gmail address>
SMTP_PASSWORD=<gmail app password>
ALERT_EMAIL_FROM=<gmail address>
ALERT_EMAIL_TO=<recipient>
```

### Production
```bash
# docker-compose.yml additions (see spec/dashboard/06_architecture.md)
docker-compose up dashboard-api dashboard-ui
```

---

## Running the migration

```bash
alembic upgrade head
# Creates: system_metrics, event_stream, alert_events

# Verify:
psql $DATABASE_URL -c "\dt system_metrics event_stream alert_events"

# Verify Streamlit still works after migration:
streamlit run execution/dashboard.py --server.headless true
```

---

## Alert response procedures

### CRITICAL: queue_lag_exceeded

**Symptoms**: Email received; health dashboard shows lag > 300 s; pending jobs not being claimed.

1. Check `active_workers` tile. If 0: restart the worker service.
2. Check Redis connectivity: `redis-cli -u $REDIS_URL ping`. If down: restart Redis; worker will reconnect.
3. Check `stuck_job_count`. If high: review job types. Common cause: a job is repeatedly failing and consuming retry slots.
4. Check Exceptions section for `call_processing_failed` or `send_sms_failed` spikes that may be blocking the queue.
5. If workers are running and Redis is healthy but lag persists: scale workers horizontally or investigate a specific job type that is slow.

**Resolution**: lag drops below threshold → resolve email sent automatically.

---

### CRITICAL: worker_offline

**Symptoms**: Email received; health dashboard shows `active_workers = 0`.

1. Check worker process status: `docker ps | grep worker` or `systemctl status cora-worker`.
2. Restart worker: `docker-compose restart worker`.
3. Confirm at least one `scheduled_jobs` row transitions to `running` within 30 seconds.
4. If worker crashes immediately on start: check logs for import errors or database connection failures.

---

### CRITICAL: ghl_auth_failure

**Symptoms**: Email received; Exceptions section shows `ghl_auth_failed` exceptions.

1. Check `GHL_API_KEY` in environment: confirm it has not expired (GHL API keys can rotate).
2. Check `GHL_LOCATION_ID`: confirm it matches the active GHL location.
3. In GHL dashboard: Settings → Integrations → API Keys → verify key is active.
4. If key expired: generate a new key, update `GHL_API_KEY` in environment, restart API and worker.
5. After fix: retry affected exceptions via the Exceptions action button.

---

### WARNING: error_rate_spike

**Symptoms**: Email received; error rate > 20%.

1. Open Exceptions section. Filter by `status = open`, sort by `created_at DESC`.
2. Identify the dominant exception type. Common cases:
   - `call_processing_failed`: Synthflow webhook payload issue or Postgres write failure.
   - `crm_task_failed`: GHL API issue (see ghl_auth_failure runbook).
   - `ghl_vm_message_update_failed`: GHL field ID mismatch (check settings for `GHL_FIELD_*` env vars).
   - `send_sms_failed` / `send_email_failed`: OpenAI failure or `generate_vm_followup` timeout.
3. Retry a batch of exceptions if the root cause is transient (API timeout, network blip).
4. If root cause is a code bug: fix, deploy, then retry.

---

### WARNING: exception_spike

**Symptoms**: Email received; open exception count > 10.

1. Open Exceptions section. Group by type.
2. If one type dominates: follow the relevant runbook entry above.
3. If exceptions are spread across types: may indicate a broader infrastructure issue (Postgres, Redis, GHL all degraded simultaneously). Check infrastructure health.
4. Resolve or ignore exceptions that are confirmed stale or no longer actionable.

---

## Operator action procedures

### Retry a failed job
1. Exceptions section → find the exception.
2. Click "Retry now" or "Retry in N min".
3. Dashboard POST to `/dashboard/actions/retry` with `delay_minutes`.
4. A new `scheduled_jobs` row is created. Worker picks it up on next cycle.
5. Confirm: exception row transitions to `resolved`; new job row visible in Queue section.

### Cancel future jobs for a lead
Use when a lead must be removed from all automated follow-up immediately (e.g., incorrect enrollment, manual resolution).

1. Lead Journey → locate the lead by phone.
2. Exceptions section → find the exception (if any) and use "Cancel future jobs".
   Or: POST `/dashboard/actions/cancel` with `contact_id`.
3. All `pending` jobs for the lead are marked `cancelled`.
4. No new outbound calls, SMS, or emails will be scheduled.
5. Confirm in Queue section: all jobs for that contact_id show `cancelled`.

### Force finalize a lead
Use when a lead has reached a terminal state outside the automated system (enrolled, withdrawn, wrong number resolved manually).

1. POST `/dashboard/actions/finalize` with `contact_id` and `reason`.
2. `lead_state.status` set to `closed`. All pending jobs cancelled.
3. Confirm in Lead State section: `status = closed`.
4. Note: if `AI Campaign = No` needs to be written to GHL, do this manually in GHL or re-enable GHL writes and trigger via a custom job.

---

## Upgrading the dashboard UI

```bash
cd dashboard-ui
npm run build
# Deploy built output to static hosting or restart the Next.js server process
docker-compose restart dashboard-ui
```

No database migrations are required for frontend-only changes.

---

## Rotating SECRET_KEY

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`.
2. Update `SECRET_KEY` in environment for both `api` and `dashboard-api` services.
3. Restart both services.
4. All existing Bearer tokens are immediately invalidated. Operators must re-authenticate.
5. The existing Streamlit dashboard also uses `SECRET_KEY` — restart it or it will reject tokens until restarted.

---

## Database cleanup

The metrics collector runs a weekly cleanup automatically. To run manually:

```sql
-- Delete system_metrics older than 30 days
DELETE FROM system_metrics WHERE recorded_at < now() - INTERVAL '30 days';

-- Delete event_stream older than 7 days
DELETE FROM event_stream WHERE created_at < now() - INTERVAL '7 days';

-- Resolved alert_events older than 90 days (optional)
DELETE FROM alert_events WHERE status = 'resolved' AND resolved_at < now() - INTERVAL '90 days';
```

---

## Disabling the dashboard system

The dashboard API, metrics collector, and alerting are fully decoupled from the main processing path. To disable:

1. Stop `dashboard-api` service: `docker-compose stop dashboard-api`.
2. Stop `dashboard-ui` service: `docker-compose stop dashboard-ui`.
3. The `metrics_jobs` scheduler will stop self-rescheduling once its last scheduled run completes — or cancel its pending `scheduled_jobs` row manually.

The main API, worker, and Streamlit dashboard are unaffected.
