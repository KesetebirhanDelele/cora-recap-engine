# spec/dashboard/00_overview.md

## Product one-liner
Cora Production Dashboard is a Next.js + FastAPI system that provides real-time operational monitoring, business KPI reporting, AI performance tracking, operator control actions, and email alerting for the Cora Outbound Recap Engine — backed exclusively by Postgres and Redis, with no external observability tools.

## Position relative to existing Streamlit dashboard
The existing Streamlit dashboard (`execution/dashboard.py`) remains unchanged and continues to run in parallel. This system is a net-new addition, not a replacement or modification. Both systems read from the same Postgres database. Neither system writes to the other's tables.

## Target users
- **Ops/Admin**: monitor system health, manage exceptions, inspect queue state, trigger operator actions.
- **Admissions / Sales Managers**: track campaign funnel, conversion KPIs, voicemail recovery rates.
- **Engineering**: investigate pipeline failures, trace per-call execution, inspect AI performance.

## Top 5 user journeys

### 1. System health at a glance
1. Operator opens dashboard home.
2. Health tiles show: queue lag, error rate, worker count, API latency, and active exception count.
3. Live activity feed shows job_started / job_completed / exception_created events in real time.
4. If queue lag exceeds threshold, a banner alert fires and an email is sent.

### 2. Per-lead pipeline trace
1. Operator searches by phone number or contact_id.
2. Full pipeline trace renders: call receipt → AI analysis → CRM write → campaign assignment → follow-up scheduling.
3. Each step shows timestamp, duration, outcome, and failure detail if applicable.
4. Operator can trigger retry, cancel, or force-finalize directly from the trace view.

### 3. Exception triage
1. Exception queue shows all open exceptions grouped by root cause type.
2. Operator selects an exception, reads context JSON, and chooses an action (retry now, retry in N min, cancel future jobs, force finalize, resolve, ignore).
3. Every action is audit-logged with operator ID.
4. Exception count metric updates within 2 seconds.

### 4. Campaign KPI review
1. Manager filters by campaign (New Lead / Cold Lead) and date range.
2. Funnel shows: leads entered → calls attempted → voicemails → completions → intent signals → enrolled.
3. Tier distribution shows how many leads are at each VM tier.
4. AI performance panel shows transcript quality, blank rate, and intent detection confidence.

### 5. Alert response
1. Alert email arrives with severity, metric name, current value, and threshold.
2. Operator clicks deep link to the relevant dashboard section.
3. Alert is acknowledged in the dashboard; acknowledgement is audit-logged.
4. Alert is suppressed for the configured deduplication window after acknowledgement.

## System boundary

### In scope
- Next.js frontend application (structure and API contract only — no UI code generated).
- FastAPI dashboard API service (`app/api/routes/dashboard_v2.py`).
- Postgres-backed metrics, event stream, alert events, and dashboard audit tables.
- Redis Pub/Sub for real-time event delivery; polling fallback when Redis is unavailable.
- Email alerting via SMTP with configurable thresholds, deduplication, and templates.
- All operator actions (retry, cancel, finalize, resolve, ignore) routed through the API with audit logging.
- Shadow mode awareness: dashboard correctly represents shadow-intercepted actions without treating them as live sends.

### Out of scope
- Modifying or replacing the existing Streamlit dashboard.
- Prometheus, Grafana, Datadog, Sentry, or any paid observability tool.
- Slack, PagerDuty, or webhook-based alerting (email only).
- Writing to GHL, Synthflow, or OpenAI from the dashboard.
- Generating AI content from the dashboard (preview remains in Streamlit Lead Journey for now).
- Building a custom dialer or replacing Synthflow.

## Success metrics
1. Queue lag alert fires within 30 seconds of threshold breach.
2. Exception triage time (open → resolved) decreases versus Streamlit-only baseline.
3. Pipeline trace renders full call lifecycle within 3 seconds for any lead.
4. Alert email deduplication prevents duplicate emails for the same event within the configured window.
5. Zero operator actions bypass audit logging.
6. Dashboard reflects Postgres truth with at most 2-second polling lag.
