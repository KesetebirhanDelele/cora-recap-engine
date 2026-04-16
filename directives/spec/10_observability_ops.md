# spec/10_observability_ops.md

## Logs
- event receipt
- webhook payload normalisation (campaign_name override from Agent field, contact_id derivation)
- dedupe decision
- AI job execution
- summary consent result
- task creation result
- tier transition
- outbound call / SMS / email scheduled or intercepted (shadow mode)
- campaign switch applied or deferred (voicemail-sequence guard)
- exception created/resolved

## Metrics
- conversion rate
- callback completion rate
- duplicate rate
- task success rate
- summary writeback rate
- exception volume
- queue lag
- dependency error rate

## Alerts

### Active alert types (implemented)

| Alert type | Trigger metric | Default threshold | Severity |
|---|---|---|---|
| `queue_lag_exceeded` | `queue_lag_seconds` — age of the oldest past-due pending job | 300 s | warning |
| `error_rate_spike` | `error_rate` — failed jobs / total jobs (last hour) | 0.20 (20%) | warning |
| `exception_spike` | `open_exception_count` | 10 open exceptions | warning |
| `worker_offline` | `active_workers` — RQ workers connected | 0 | critical |
| `ghl_auth_failure` | `ghl_auth_failure` metric via health check | any failure | critical |

**Important:** `queue_lag_seconds` is the age of the *oldest* past-due pending job
(`MIN(run_at)` for `status='pending' AND run_at <= NOW()`). It is **not** the backlog count
shown in the Queue Health UI (which is `stuck_job_count + expired_lease_count`).
A large backlog of future-dated jobs does not trigger the alert; only jobs that are
overdue by more than the threshold do.

### Alert dedup and resolution
- One email per breach; within `ALERT_DEDUP_WINDOW_SECONDS` only `last_seen_at` is updated.
- When a metric clears, a resolution email is sent.
- `alert_events` table records all firings (active/resolved).

### SMTP email delivery
Alerts send email via SMTP (`app/services/alerting.py`). Required settings:
```
SMTP_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=<gmail address>
SMTP_PASSWORD=<16-char App Password — NOT the account password>
ALERT_EMAIL_FROM=<from address>
ALERT_EMAIL_TO=<recipient address>
```

**Gmail requires an App Password**, not the account password, for SMTP auth.
Create one at: Google Account → Security → 2-Step Verification → App passwords.
Using the account password causes `535 Username and Password not accepted`.

### Metrics collection
`collect_metrics_job` runs every 60 s as a self-rescheduling RQ job on the `default` queue.
It is started once at worker startup by `start_metrics_scheduler()` (called by the
`default`/`all` role only). Each run writes one `system_metrics` row per tracked metric
and calls `evaluate_alerts()`.

Thresholds are settings-driven:
```
ALERT_QUEUE_LAG_THRESHOLD_SECONDS=300
ALERT_ERROR_RATE_THRESHOLD=0.20
ALERT_EXCEPTION_COUNT_THRESHOLD=10
ALERT_DEDUP_WINDOW_SECONDS=3600
METRICS_COLLECTION_INTERVAL_SECONDS=60
```

## Dashboard — Legacy Streamlit (read-only monitoring)
Streamlit dashboard at `execution/dashboard.py`. Run with `streamlit run execution/dashboard.py` (Postgres only required). This dashboard is superseded by Dashboard v2 for all real-time operational use.

Sections:
- **Overview** — metrics tiles (calls 24 h, shadow actions, open exceptions, failed jobs); bar charts by job status and shadow action type
- **Campaign Overview** — all leads with next scheduled action; filterable by date window; shows campaign, status, effective next action time and channel
- **Trends** — date-range trend charts per campaign (New Lead, Cold Lead, Inbound): total calls, errors, % completed call, % Goodbye; granularity: day/week/month
- **Recent Calls** — call events joined to lead state, transcript preview
- **Lead State** — filterable by status and campaign
- **Shadow Actions** — intercepted outbound actions (outbound_call, sms, email) when shadow mode is on
- **Scheduled Jobs** — queue state, filterable by status and job type
- **Exceptions** — operator exception queue; filter by severity and status
- **Contact Drill-Down** — single contact_id view across all 6 operational tables (raw data)
- **Lead Journey** — per-lead chronological touchpoint history; filterable by phone number; shows calls, messages, campaign switches, and next scheduled action. Two-pass phone lookup: `lead_state.normalized_phone` first, then `call_events.raw_payload_json` fallback. Note: SMS and outbound calls appear in `shadow_actions` (not `outbound_messages`) when shadow mode is on.

Trends metric definitions:
- Total calls: all `call_events` rows in range for the campaign
- % Completed call: `call_events.status = 'completed'` / total
- % Goodbye: `call_events.end_call_reason ILIKE '%goodbye%'` / total
- Errors: distinct contacts with ≥1 `exceptions` row in the same date range

Operator retry/cancel/finalize actions remain API-only (Bearer token required):
- `POST /v1/exceptions/{id}/retry-now`
- `POST /v1/exceptions/{id}/retry-delay`
- `POST /v1/exceptions/{id}/cancel-future-jobs`
- `POST /v1/exceptions/{id}/force-finalize`

## Dashboard v2 — Production Console (Next.js + FastAPI)
Dashboard v2 is the primary operational and analytics console. It runs as two separate services: FastAPI on port 8001 and Next.js on port 3000.

Pages and capabilities:
- **Home** (`/`) — system health status bar (Healthy/Warning/Critical), shadow/live mode pill, active alerts, navigation cards with live badge counts
- **Campaign Overview** (`/campaign-overview`) — scheduled contacts in a date window, filterable by campaign (New Lead / Cold Lead / Unknown); campaign resolved via `campaign_name` → `lead_stage` → `voice_agent` fallback; click-through to Contact Lookup
- **Contact Lookup** (`/contact-lookup`) — full contact detail by contact_id or phone; also used as drill-down target from Campaign Overview
- **Live Activity** (`/activity`) — real-time worker event stream via WebSocket with polling fallback
- **Exceptions Monitor** (`/exceptions`) — open exception queue with Resolve / Ignore / Bulk-Ignore actions; trend chart; date/type/severity filters
- **System Anomalies** (`/system-anomalies`) — spike detection, recurring issues table, failure clusters, 14-day frequency trend
- **Queue Health** (`/queue`) — stuck jobs and expired worker leases
- **Alerts** (`/alerts`) — 5 alert types: queue_lag_exceeded, error_rate_spike, exception_spike, worker_offline, ghl_auth_failure
- **Voice Performance** (`/voice-performance`) — KPI sidebar (9 metrics + WoW%), stacked trends chart (Cold Lead blue / New Lead green / Inbound yellow), WoW waterfall, efficiency scatter
- **AI Performance** (`/ai-performance`) — blank transcript rate, unknown intent %, intent distribution, consent distribution, intent→outcome table
- **Engagement Analysis** (`/engagement-analysis`) — cross-filter analytics by campaign, call direction, and voice agent; all filters including consent distribution apply via `summary_results JOIN call_events`; Outbound direction = NOT Inbound (covers NULLs and non-inbound values)
- **Conversion Funnel** (`/conversion-funnel`) — 4-stage funnel, drop-off alert, step table, trend lines
- **CRM Health** (`/crm-health`) — GHL task/VM success rates, shadow write count
- **Lead Pipeline Trace** (`/lead/[id]`) — per-contact job timeline with shadow flags and failure reasons
- **Settings** (`/settings`) — runtime app config management (brand, messaging, thresholds); write requires Bearer token

Campaign colors in v2: Cold Lead `#2563eb` (blue), New Lead `#16a34a` (green), Inbound `#eab308` (yellow).

## Reporting observability
- reporting refresh success/failure
- KPI query latency
- filter and cross-filter interaction latency, where measurable