# spec/10_observability_ops.md

## Logs
- event receipt
- enrich success/failure
- dedupe decision
- AI job execution
- summary consent result
- task creation result
- tier transition
- callback scheduled
- shadow mirror sync result
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
- sheet mirror reconciliation drift count

## Alerts
- GHL auth failure: critical immediately
- duplicate rate spike: warning/critical thresholds
- stuck-call volume spike: warning/critical thresholds
- queue lag breach
- Postgres write failures
- sheet mirror reconciliation failures above threshold

## Dashboard scope v1 — IMPLEMENTED
Streamlit dashboard at `execution/dashboard.py`. Run with `streamlit run execution/dashboard.py` (Postgres only required).

Sections:
- **Overview** — metrics tiles (calls 24 h, shadow actions, open exceptions, failed jobs); bar charts by job status and shadow action type
- **Recent Calls** — call events joined to lead state, transcript preview
- **Lead State** — filterable by status and campaign
- **Shadow Actions** — intercepted outbound actions (outbound_call, sms, email) when shadow mode is on
- **Scheduled Jobs** — queue state, filterable by status and job type
- **Exceptions** — operator exception queue; filter by severity and status
- **Contact Drill-Down** — single contact_id view across all 6 tables

Operator retry/cancel/finalize actions remain API-only (Bearer token required):
- `POST /v1/exceptions/{id}/retry-now`
- `POST /v1/exceptions/{id}/retry-delay`
- `POST /v1/exceptions/{id}/cancel-future-jobs`
- `POST /v1/exceptions/{id}/force-finalize`

## Reporting observability
- reporting refresh success/failure
- KPI query latency
- filter and cross-filter interaction latency, where measurable
- reporting reconciliation drift between Google Sheets mirror data and Postgres authoritative data