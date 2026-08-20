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

**Required `.env` settings before building the frontend:**
```
# Internal Docker hostname — must NOT be localhost (that resolves to the frontend container itself)
DASHBOARD_API_URL=http://dashboard-api:8001
WS_URL=ws://<server-ip>:8001

# Allow the Next.js origin so WebSocket connections aren't blocked by CORS
ALLOW_ORIGINS=http://<server-ip>:3000
```

`DASHBOARD_API_URL` is baked into the Next.js image at build time via `next.config.js`. If it is wrong, all server-side data fetches (SSR) will fail silently and browser POSTs will never reach the server.

```bash
cd /opt/cora-recap-engine
# Pull latest code
git pull

# Rebuild and restart — only dashboard-api and frontend need rebuilding for most changes
docker compose up -d --build dashboard-api frontend

# Rebuild only dashboard-api (no frontend code changes)
docker compose up -d --build dashboard-api
```

> **Important — code is baked into the image, not volume-mounted.**
> `docker compose restart <service>` only restarts the running container — it does NOT pick up new code. You must always run `docker compose up -d --build <service>` (or `--no-cache` if Docker is serving a stale layer) after `git pull` for code changes to take effect.
>
> If a rebuild still serves old code (visible via stale UI text or behaviour), use `--no-cache` to force a full rebuild:
> ```bash
> docker compose build --no-cache frontend && docker compose up -d --no-deps frontend
> docker compose build --no-cache dashboard-api && docker compose up -d --no-deps dashboard-api
> ```

---

## DB Explorer (browser-based SQL tool)

The dashboard includes a built-in SQL query interface at `/db-explorer` (Operations section on the home page). It does not require an SSH tunnel and works directly in the browser.

Features:
- Left sidebar: all Postgres tables with row estimates (click any table to auto-fill `SELECT * FROM <table> LIMIT 100`)
- SQL editor: multi-line textarea, `Ctrl+Enter` to run
- Results table: scrollable, sticky column headers, striped rows, `NULL` displayed clearly, cell tooltip on hover
- **Download Excel** button: appears after any successful SELECT — downloads a `.csv` file that Excel opens natively
- Max 500 rows per query; `truncated` warning shown if result is larger
- DML (INSERT/UPDATE/DELETE) is supported — result shows rows affected

Auth: the query endpoint (`POST /dashboard/db/query`) requires a valid Bearer token (same token used for other write actions in the dashboard).

---

## Querying Postgres directly on the server

> **Tip:** The `/db-explorer` dashboard page provides an embedded SQL runner accessible directly from the browser — no SSH tunnel required for most queries. Use the CLI approach below for bulk exports, scripting, or when the dashboard is unavailable.

All `docker compose` commands must be run from the project directory:

```bash
cd /opt/cora-recap-engine
```

Running them from any other directory produces: `no configuration file provided: not found`.

### Interactive shell

```bash
docker compose exec postgres psql -U postgres -d cora
```

Useful psql meta-commands:

| Command | What it does |
|---|---|
| `\dt` | List all tables |
| `\d <table>` | Describe a table (columns, types, indexes) |
| `\q` | Exit |

### One-liner queries

```bash
# Stuck jobs (pending past run_at by more than 10 minutes)
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, job_type, contact_id, run_at,
         EXTRACT(EPOCH FROM (NOW() - run_at))::int AS lag_seconds
  FROM scheduled_jobs
  WHERE status = 'pending' AND run_at < NOW() - INTERVAL '10 minutes'
  ORDER BY run_at;"

# Expired leases (running jobs with expired lease)
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, job_type, claimed_by,
         EXTRACT(EPOCH FROM (NOW() - lease_expires_at))::int AS age_seconds
  FROM scheduled_jobs
  WHERE status = 'running' AND lease_expires_at < NOW();"

# Active alerts
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT alert_type, severity, message, created_at
  FROM alert_events WHERE status = 'active';"

# Open exceptions
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT type, severity, status, created_at
  FROM exceptions WHERE status = 'open'
  ORDER BY created_at DESC LIMIT 20;"

# Recent call events
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, status, duration_seconds, detected_intent, created_at
  FROM call_events ORDER BY created_at DESC LIMIT 10;"

# All scheduled jobs for a specific contact
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT id, job_type, status, run_at, claimed_by
  FROM scheduled_jobs WHERE contact_id = '<contact_id>'
  ORDER BY created_at DESC;"

# Lead state for a specific contact
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT * FROM lead_state WHERE contact_id = '<contact_id>';"

# Recent audit log (operator actions)
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT entity_type, entity_id, action, operator_id, created_at
  FROM audit_log ORDER BY created_at DESC LIMIT 20;"
```

### Data persistence

Postgres data lives in a named Docker volume (`postgres_data`), stored on the host at `/var/lib/docker/volumes/cora-recap-engine_postgres_data`. It is **independent of any container or image**. Rebuilding services, pulling new code, or restarting containers never touches it.

**The only destructive commands are:**
- `docker compose down -v` — the `-v` flag explicitly removes volumes. **Never run this in production.**
- `docker volume rm cora-recap-engine_postgres_data` — same effect.

Safe commands (data is always preserved):
- `docker compose up -d --build <service>` — rebuilds and restarts a service
- `docker compose restart <service>` — restarts without rebuild
- `docker compose down` — stops and removes containers, volumes untouched

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

## Lead Lifecycle Monitor — known issues and fixes

### Tier-3 leads showing as "Active" (fixed 2026-04-28)
`_finalize_campaign()` writes to GHL but does not update `lead_state.status` to `'closed'`. Leads that complete the voicemail sequence remain with `status = NULL` and `ai_campaign_value = '3'`. Before the fix, the status badge and summary counts treated these as Active.

**Fix applied to**:
- `dashboard-ui/app/lead-lifecycle/page.tsx` — `statusBadge()` now checks `vm_tier === "3"` before the Active fallback
- `app/services/lead_lifecycle.py` — summary counts and row filters include `ai_campaign_value = '3'` in the finalized definition
- `app/services/dashboard_metrics.py` — `finalized_today` and `finalized_yesterday` include `ai_campaign_value = '3'`

**If this regression reappears**: verify all three files include the `ai_campaign_value = '3'` condition and rebuild `dashboard-api` + `frontend`.

### Lead Lifecycle nav card showing stale "in VM" count
The nav card previously used `in_vm_sequence` as its primary metric. Changed to `active_leads` (2026-04-28) for a more meaningful at-a-glance count.

**Location**: `dashboard-ui/lib/indicators.ts` — `CARD_METRIC_MAP["/lead-lifecycle"]`

If the card label reverts to "in VM" after a deploy, check that `indicators.ts` has `key: "active_leads"` and rebuild the frontend.

### Leads stuck mid-voicemail sequence (no pending job, no call_event)
Caused by Synthflow HTTP step webhook drops — either from burst concurrency (all calls firing at exact window-open second) or transient Synthflow reliability failures. See `spec/14_synthflow_integration_addendum.md` for the full diagnosis procedure.

**Automatic recovery (2026-05-01+)**: The `auto_webhook_recovery` job runs every 5 minutes and resolves failures automatically. For each webhook failure it:
1. Searches Synthflow `GET /v2/calls` by phone number + campaign model ID within a 3-hour window of the call's execution time.
2. Terminal call found → runs the full AI + GHL pipeline via `recover_missed_webhook()`.
3. No call found → re-schedules the call via `advance_stale_lead("no_answer")`.

Recovered leads disappear from the Webhook Delivery panel on the next page refresh (both `recover_missed_webhook` and `advance_stale_lead` write audit entries that the panel excludes).

The `auto_webhook_recovery` job is visible in `scheduled_jobs` with `job_type='auto_webhook_recovery'` and `entity_id='webhook_recovery'`. If it is missing after a restart, restarting `worker-default` re-creates it.

**Manual recovery (operator-initiated)**: Use the **Webhook Delivery — 24h** panel in Queue Health (`/queue`) when you want to override the auto-recovery decision or act immediately. Three inline action buttons appear per row:

| Button | When to use | Effect |
|---|---|---|
| **VM Left** | Synthflow logs show a voicemail was left | Advances tier (or finalizes at tier 2); schedules next call |
| **No Answer** | Synthflow logs show call did not connect | Schedules retry; or closes lead on second consecutive no-answer |
| **Call Completed** | Synthflow logs show call completed with transcript | Expands a call_id input; fetches call from Synthflow API and replays full pipeline (AI analysis, GHL updates) |

For **Call Completed**: get the Synthflow call_id from the Logs page in the Synthflow dashboard (not the internal job_id). Paste it in the inline input and press Enter or "Fetch →".

**Detection query** (for incidents older than 24h or for bulk review):
```sql
SELECT DATE_TRUNC('minute', sj.run_at) AT TIME ZONE 'America/Chicago' AS minute_cst,
       ls.ai_campaign_value AS vm_tier, COUNT(*) AS leads
FROM lead_state ls
JOIN LATERAL (
    SELECT run_at FROM scheduled_jobs
    WHERE entity_id = ls.contact_id AND job_type = 'launch_outbound_call' AND status = 'completed'
    ORDER BY run_at DESC LIMIT 1
) sj ON TRUE
WHERE ls.ai_campaign_value IN ('0','1','2')
  AND (ls.status IS NULL OR ls.status NOT IN ('closed','terminal'))
  AND ls.do_not_call IS NOT TRUE
  AND NOT EXISTS (SELECT 1 FROM scheduled_jobs WHERE entity_id = ls.contact_id
                    AND job_type = 'launch_outbound_call' AND status IN ('pending','claimed'))
  AND NOT EXISTS (SELECT 1 FROM call_events ce WHERE ce.contact_id = ls.contact_id
                    AND ce.created_at >= sj.run_at)
GROUP BY 1, 2 ORDER BY 1 DESC, 2;
```

**Bulk recovery (incidents > 24h old)**:
- Tier-2 stuck leads (would have finalized): `execution/finalize_stuck_tier2.py --live`
- Tier-0/1 stuck leads (need next call): `execution/reschedule_burst_tier1.py --live`

Always dry-run first. Copy scripts into the container with `docker compose cp` since images are baked.

**Panel exclusion logic**: The Webhook Delivery panel automatically hides resolved leads — those with `lead_state.status IN ('terminal', 'closed')` or with an active `pending`/`claimed`/`running` job. If a lead disappears from the panel after an action, that is expected behaviour.

---

## Alert response procedures

### CRITICAL: queue_lag_exceeded

**Symptoms**: Email received; health dashboard shows lag > 300 s; pending jobs not being claimed.

1. Check `active_workers` tile. If 0: restart the worker service.
2. Check Redis connectivity: `redis-cli -u $REDIS_URL ping`. If down: restart Redis; worker will reconnect.
3. Check `stuck_job_count`. If high: review job types. Common cause: a job is repeatedly failing and consuming retry slots.
4. Check Exceptions section for `call_processing_failed` or `send_sms_failed` spikes that may be blocking the queue.
5. **Check whether outbound campaigns are paused** — if `outbound_campaigns_paused = true`, held `launch_outbound_call` jobs re-defer themselves every 60 s. Their `run_at` is always within 60 s of `now()`, so `queue_lag_seconds` should be < 60. If you see lag > 300 s alongside an outbound-campaign pause, first confirm the code version has the `defer_seconds=60` fix (`git log --oneline | grep defer`). If the fix is not deployed, held jobs keep their original `run_at` (past) and appear permanently overdue — deploy the fix to clear the alert.
6. If workers are running and Redis is healthy but lag persists: scale workers horizontally or investigate a specific job type that is slow.

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

1. Check `GHL_API_KEY` in environment — it is a **Private Integration JWT token**, not a simple key. These expire or can be revoked.
2. In GHL: Settings → Integrations → Private Integrations → verify the integration is active and the token has not been revoked.
3. Check `GHL_LOCATION_ID` matches the active GHL location.
4. Verify the token has all required scopes: `contacts.readonly`, `contacts.write`, `locations/tasks.write`, `locations/customFields.readonly`.
5. If expired/revoked: regenerate the token, update `GHL_API_KEY` in `/opt/cora-recap-engine/.env`, then restart API and worker:
   ```bash
   cd /opt/cora-recap-engine
   docker compose restart api worker-default worker-ai
   ```
6. After fix: retry affected exceptions via the Exceptions action button.
7. Verify fix:
   ```bash
   python execution/test_scripts/test_ghl_writes.py --phone +1XXXXXXXXXX
   ```

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

## Go-Live Procedure (shadow → live)

All mode flags are DB-backed and can be toggled from the **System Controls** page (`/system-controls`) without any `.env` edit or container restart. Changes take effect on the next job execution.

### Pre-flight checklist

1. Verify `test_ghl_writes.py` passes against the production contact:
   ```bash
   cd /opt/cora-recap-engine
   python execution/test_scripts/test_ghl_writes.py --phone +1XXXXXXXXXX
   # Expected: ALL CHECKS PASSED
   ```
2. Check System Controls → Pre-flight status panel: all items green or yellow (no red).
3. Confirm no active `ghl_auth_failure` alerts in the Alerts page.
4. Confirm Zapier enrollment is stopped / no new leads being enrolled.
5. Wait for any in-flight Zapier-triggered calls to complete (check Live Activity for idle state).

### Cutover sequence (from System Controls dashboard)

1. Navigate to `http://<server-ip>:3000/system-controls`.
2. Set **GHL Write Mode** → `live`.
3. Enable **GHL Write: Contact Fields** → on.
4. Enable **GHL Write: Tasks** → on.
5. Enable **GHL Write: Summary** → on.
6. Enable **GHL Write: Campaign State** → on.
7. Enable **GHL Write: Finalization** → on.
8. Set **Shadow Mode** → off (enables outbound calls / SMS / email).
9. Confirm each toggle shows the updated state.

### Verify after cutover

```bash
# Watch worker logs for live write confirmations
docker compose logs -f worker-default

# Expected log lines on next call processed:
# INFO GHL update_contact_fields | contact_id=...
# INFO GHL create_task | contact_id=...
```

Check GHL contact for the test lead — fields should have real values, not stale test data.

### Rollback

Return to System Controls and set **GHL Write Mode** → `shadow` and **Shadow Mode** → on. No restart required.

---

## Pre-Live GHL Write Integration Test

Before go-live, run `test_ghl_writes.py` to verify all field writes against a real GHL contact:

```bash
cd /opt/cora-recap-engine
python execution/test_scripts/test_ghl_writes.py --phone +1XXXXXXXXXX
# or by contact ID:
python execution/test_scripts/test_ghl_writes.py --contact-id <GHL_CONTACT_ID>
```

The script:
1. Looks up the contact in GHL
2. Fetches all field definitions from the location (`GET /locations/{id}/customFields`)
3. Writes test values to every field Cora uses
4. Reads back and verifies each write
5. Creates a test task (delete it from GHL afterwards)
6. Restores original field values

**Required Private Integration scopes:** `contacts.readonly`, `contacts.write`, `locations/tasks.write`, `locations/customFields.readonly`

---

## Setting up the Dashboard Token (required for all write actions)

All operator action endpoints (`/dashboard/actions/*` and `POST /dashboard/settings`) require `Authorization: Bearer <SECRET_KEY>`. The frontend reads this token from `localStorage["dashboard_token"]`.

**First-time setup (or after a `SECRET_KEY` rotation):**
1. Navigate to `http://<host>:3000/settings`.
2. Find the **Dashboard Token** card at the top of the page.
3. Paste the value of `SECRET_KEY` from the server's `.env` file into the password input.
4. Click **Save token**. The green "Token is set" indicator confirms it is stored.
5. The token persists in the browser's `localStorage` until the browser storage is cleared or the token is explicitly deleted from that field.

**Verifying the token is working:**
```bash
# On the server, extract the key:
SECRET=$(grep '^SECRET_KEY=' /opt/cora-recap-engine/.env | cut -d= -f2)

# Confirm the endpoint accepts it (expect 422, not 403):
curl -s -w "\n%{http_code}" -X POST http://localhost:8001/dashboard/actions/acknowledge-alert \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{}'
# Expected: {"detail": ...} with HTTP 422 (body validation error, not auth error)
```

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

### Pause outbound campaigns (New Lead / Cold Lead only)
Use when you need to temporarily halt all New Lead and Cold Lead outbound activity — calls, voicemails, AI analysis, SMS, email, CRM writes, and nurture graduation — while keeping Inbound processing fully live.

1. Navigate to `http://<host>:3000/system-controls`.
2. Locate the **Outbound Campaign Pause** section.
3. Click the orange **Pause** button. The status banner turns orange and shows "Outbound campaigns paused — New Lead & Cold Lead calls/SMS/email held. Inbound unaffected."
4. Workers will detect the flag on the next job pickup (within one scheduler tick, ≤ 30 s). New Lead and Cold Lead jobs are released back to `pending` with `run_at = now() + 60s`; they are not lost.
5. Inbound call processing, AI analysis, and GHL writes continue without interruption.

**To resume**: Click the green **Resume** button in the same section. Held jobs have `run_at` at most 60 s in the future and drain automatically — no manual re-queue needed.

**While paused**:
- `queue_lag_seconds` remains 0 — held jobs are re-deferred every 60 s so they are never permanently overdue.
- The status banner shows `◉ Campaign Paused` (orange) on every dashboard page.
- The `auto_webhook_recovery` job is unaffected — it checks Synthflow for missed calls and recovers them regardless of this flag.

**Note**: If `system_paused = true`, the Resume button is hidden (resuming outbound campaigns while the whole system is paused has no visible effect). Clear the system pause first.

---

### Acknowledge an alert
Use when an alert is active but the underlying issue is already known and being worked. Acknowledging moves the alert off the Active tab so it does not distract from new signals, without suppressing the audit record.

1. Navigate to `http://<host>:3000/alerts`.
2. Open the **Active** tab.
3. Locate the alert and click **Acknowledge**.
4. The alert is immediately removed from the Active tab (optimistic UI).
5. Confirm: switch to the **Acknowledged** tab — the alert row appears there.
6. Backend: `PUT alert_events SET status='acknowledged'`; one `audit_log` row written.
7. **Note**: acknowledging does not suppress the next alert email if the same threshold is breached again after the dedup window.

### Cancel a stuck job from Queue Health
Use when the Queue Health page (`/queue`) shows a stuck pending job and the associated contact should no longer be processed (e.g. incorrect enrollment, manual resolution, or a job that will never clear without intervention).

1. Navigate to `http://<host>:3000/queue`.
2. In the **Stuck Jobs** table, locate the job. If the **Contact** column shows a contact ID, a **Cancel jobs** button appears in the **Action** column.
3. Click **Cancel jobs**.
4. Backend: `POST /dashboard/actions/cancel` with `contact_id` and `reason="operator_cancelled_from_queue_health"`. All `pending` jobs for that contact are marked `cancelled`.
5. The page re-fetches automatically. The cancelled contact's jobs should no longer appear in the stuck list.
6. **No action is available** for rows without a contact ID, or for entries in the **Expired Leases** table — expired leases are auto-recovered by the worker's `recover_expired_claims` routine.

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

## Known production bug patterns (resolved — documented for future reference)

### "Failed to fetch" on write actions from the browser
**Symptom**: System Controls / operator action buttons show `Failed: TypeError: Failed to fetch`. Read pages (health, metrics) work fine.

**Root cause**: `NEXT_PUBLIC_API_URL` was baked in as `http://localhost:8001` at build time. Browser-side calls sent requests to the user's local machine (not the server), which has no port 8001.

**Fix applied (2026-04-18)**: Added Next.js rewrites in `next.config.js` so all `/dashboard/*` calls from the browser go to port 3000 (same origin) and are proxied server-side to `http://dashboard-api:8001`. `lib/api.ts` now uses `window.location.origin` in the browser. `docker-compose.yml` default `DASHBOARD_API_URL` changed to `http://dashboard-api:8001`.

**If this recurs**: Check `DASHBOARD_API_URL` in `.env` is `http://dashboard-api:8001` (not `localhost`). Rebuild the frontend image.

---

### "Internal Server Error" on POST /dashboard/mode (Go Live button)
**Symptom**: Clicking "Go Live" in System Controls shows `Failed: Internal Server Error`.

**Root cause**: `psycopg2` fails to translate named parameters when `::` cast follows immediately. `:ctx::jsonb` was left untranslated in the SQL → `syntax error at ":"`.

**Fix applied (2026-04-18)**: Changed `:ctx::jsonb` to `CAST(:ctx AS jsonb)` in the `audit_log` INSERT in `dashboard_v2.py`.

**Prevention**: Never write `:param::type` in SQLAlchemy `text()` blocks. Always use `CAST(:param AS type)`.

---

### Status bar shows "◉ Shadow" after going live
**Symptom**: System Controls shows LIVE, but the home page header still shows `◉ Shadow | GHL: shadow`.

**Root cause**: `get_health()` returned `settings.shadow_mode_enabled` and `settings.ghl_write_mode` from the `.env` file. System Controls writes to `app_config` (DB), not `.env`. The two sources diverged.

**Fix applied (2026-04-18)**: `get_health()` now calls `get_mode_flags(session, settings)` (DB-first lookup) for these two fields, identical to how `GET /dashboard/mode` reads them.

**Prevention**: Any endpoint that surfaces operational mode state must use `get_mode_flags()`, never `settings.*` directly for fields that are DB-backed.

---

### InvalidRegularExpression on /dashboard/recent-calls
**Symptom**: `GET /dashboard/recent-calls` returns 500. Postgres log shows `ERROR: invalid regular expression: quantifier operand invalid`.

**Root cause (1)**: The SQL regex was wrapped in `E'...'` (PostgreSQL escape string). PG strips unrecognised escape sequences — `E'^\+?...'` becomes `^+?...` in the regex engine. `+?` tries to quantify the zero-width anchor `^` → invalid.

**Root cause (2)**: `{7,}` inside a Python f-string was written without doubling the braces. Python evaluates `{7,}` as the tuple `(7,)`, producing `(7,)` in the SQL string — not a valid regex quantifier.

**Fix applied**: Use plain `'...'` string literals (not `E'...'`). Write `{{7,}}` in Python f-strings so the SQL receives `{7,}`.

**Prevention**: See `spec/dashboard/03_constraints.md` — SQL authoring rules section.

---

### "Internal Server Error" on VM Left / No Answer buttons
**Symptom**: Clicking "VM Left" or "No Answer" in the Webhook Delivery panel returns `Error: Internal Server Error`.

**Root cause (1)**: `_finalize_lead` and `_close_lead` in `stale_recovery.py` called `ghl.update_contact_fields(contact_id, resolved, flags)` — passing `flags` as a positional arg. `mode_flags` is keyword-only in `GHLClient.update_contact_fields` (declared after `*`), raising `TypeError`.

**Fix applied (2026-04-30)**: Changed to `ghl.update_contact_fields(contact_id, resolved, mode_flags=flags)` in both functions.

**Root cause (2)**: `contact_id` in `lead_state` is a phone number (e.g. `+18014002089`). GHL's `PUT /contacts/:id` requires the real UUID. Passing the phone number directly causes GHL 400 `"Contact with id +18014002089 not found"`.

**Fix applied (2026-04-30)**: Added `_resolve_ghl_contact_id(ghl, contact_id)` in `stale_recovery.py` — detects digit-only strings and calls `ghl.search_contact_by_phone()` to get the real UUID before any GHL write. Mirrors the pattern already used in `crm_jobs.py`.

**Prevention**: Any code that writes to GHL using a `contact_id` from `lead_state` must resolve it via `search_contact_by_phone()` first — the DB stores phone numbers, not GHL UUIDs.

---

### Resolved leads reappearing in Webhook Delivery panel after operator action
**Symptom**: After clicking VM Left, No Answer, or Call Completed, the row disappears momentarily then reappears on the next 30-second refresh.

**Root cause**: The webhook failure query (`get_webhook_failures`) returned all unmatched `launch_outbound_call` jobs in the last 24 hours without checking whether the operator had already resolved them. After a manual advance, the lead is `terminal`/`closed` or has a new pending job — but the old completed job row still exists with no `call_events`, so it kept appearing.

**Fix applied (2026-04-30)**: Both the `failures` list and `summary` row in `get_webhook_failures()` now exclude leads whose `lead_state.status IN ('terminal','closed')` and leads with an active `pending`/`claimed`/`running` `launch_outbound_call` job.

---

### "Call Completed" recovery returning `unknown_call_status` exception
**Symptom**: Using the Call Completed button (or `POST /dashboard/actions/recover-call-webhook`) schedules the job successfully but an `unknown_call_status` exception appears in the Exceptions panel with `"status": "ok"`.

**Root cause**: Synthflow's `GET /v2/calls/{call_id}` returns `{"status": "ok", "data": [{...call record...}]}` — the `data` field is an **array**, not a dict. The original envelope-unwrap code only handled the dict case, so the outer envelope leaked through. `normalize_synthflow_outcome()` found `status: "ok"` (the HTTP-level API success flag, not a call outcome) and created an `unknown_call_status` exception.

**Fix applied (2026-04-30)**:
- `app/adapters/synthflow.py` `get_call()` — envelope unwrap now handles `data` as dict, list, `response.calls[]`, and `data.calls[]` patterns.
- `app/services/stale_recovery.py` `recover_missed_webhook()` — maps `"ok"` → `"completed"` (Synthflow API-level flag is not a call outcome), and sets `call_status` explicitly in the normalized payload so `process_call_event` finds it via the highest-priority alias and never reads `"ok"`.

**Prevention**: When consuming Synthflow GET responses, always use `normalize_synthflow_outcome()` to extract call status — it tries all known field aliases in priority order. Never use raw `.get("status")` against an unwrapped Synthflow response.

---

### `webhook_drop_detected` alert re-fires immediately after operator resolves it
**Symptom**: The `webhook_drop_detected` warning is resolved/acknowledged, then reappears within 60 seconds pointing to the same historical bucket.

**Root cause (re-fire)**: The dedup query in `_evaluate_webhook_drop()` only checked `status = 'active'` alerts. After the operator resolved the alert, `existing` returned `None` on the next metrics cycle, and the same failing bucket triggered a new alert.

**Root cause (false positive)**: Leads handled via VM Left / No Answer buttons (manual_advance audit entries) were still counted as "missing webhooks" because the query only joined against `call_events` rows. No `call_events` row is created by those recovery paths, so the delivery rate stayed below 80%.

**Fix applied (2026-04-30)**:
1. The dedup query now includes recently-resolved alerts within the 2-hour lookback window — any prior fire (active or resolved) suppresses a re-fire for the same period. The suppression clears automatically when the bucket falls outside the 2-hour lookback.
2. The delivery rate query now counts leads with a `manual_advance` or `manual_webhook_recovery` audit entry as "got webhook", so operator-resolved leads no longer count as missing.

**If the alert keeps firing after resolving**: wait ~30 minutes for the failing bucket to fall outside the 2-hour lookback window. Alternatively, verify `audit_log` has `action IN ('manual_advance', 'manual_webhook_recovery')` entries for the affected contacts.

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
