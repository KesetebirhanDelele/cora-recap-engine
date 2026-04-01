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

## Dashboard — IMPLEMENTED (8 sections)
Streamlit dashboard at `execution/dashboard.py`. Run with `streamlit run execution/dashboard.py` (Postgres only required).

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

## Reporting observability
- reporting refresh success/failure
- KPI query latency
- filter and cross-filter interaction latency, where measurable