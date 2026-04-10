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
- GHL auth failure: critical immediately
- duplicate rate spike: warning/critical thresholds
- stuck-call volume spike: warning/critical thresholds
- queue lag breach
- Postgres write failures

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