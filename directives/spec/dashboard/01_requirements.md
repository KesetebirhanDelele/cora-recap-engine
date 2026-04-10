# spec/dashboard/01_requirements.md

## Functional requirements

### A. System health monitoring
- Expose queue lag as the age of the oldest `scheduled_jobs` row where `status = 'pending'` and `run_at <= now()`.
- Expose job throughput as completed jobs per minute derived from `scheduled_jobs.updated_at` transitions.
- Expose error rate as the ratio of `exceptions` rows created in the last 5 minutes to total jobs completed in the same window.
- Expose worker health as the count of distinct `claimed_by` values in `scheduled_jobs` where `status = 'running'` and `lease_expires_at > now()`.
- Expose API latency as a derived p50/p95 estimate from `scheduled_jobs` claim-to-complete durations for `process_call_event` jobs.
- All health metrics must refresh within 2 seconds of the underlying Postgres state changing.

### B. Live activity feed
- Emit real-time events for: `job_started`, `job_completed`, `job_failed`, `exception_created`, `call_processed`, `campaign_switched`, `alert_triggered`.
- Each event must carry: `event_type`, `entity_type`, `entity_id`, `contact_id`, `timestamp`, and a short `message` string.
- Events must be delivered via Redis Pub/Sub when Redis is available.
- When Redis is unavailable, the frontend falls back to polling `GET /dashboard/events` every 5 seconds.
- The feed must display the last 100 events in reverse chronological order.
- Events must not be duplicated on reconnect — the client sends a `since` cursor (timestamp) and the server returns only newer events.

### C. Pipeline trace (per lead)
- Accept lookup by `contact_id` or normalized phone number.
- Return a full ordered sequence of pipeline steps: call_event received → process_call_event → update_lead_state → run_call_analysis → create_crm_task → send_student_summary → voicemail tier jobs → send_sms → send_email → update_ghl_after_vm_message.
- Each step must include: `job_type`, `started_at`, `completed_at`, `duration_ms`, `status`, `failure_reason` if failed, and a `payload_summary` (non-sensitive fields only).
- Failed steps must be visually distinct and link to the related exception record.
- Shadow-intercepted steps must be labeled as shadow with the shadow payload visible.

### D. Exception management
- List all open exceptions with filtering by: severity (`critical` / `warning`), type, entity_type, date range.
- Group exceptions by `type` to show root cause frequency.
- Expose full `context_json` per exception.
- Support all existing operator actions from the Streamlit dashboard: retry now, retry in N minutes, cancel future jobs, force finalize, resolve, ignore.
- All operator actions must be routed through the API (no direct DB writes from the frontend).
- Each action must be audit-logged with `operator_id` from the authenticated session.

### E. Queue monitoring
- Show all `scheduled_jobs` rows filterable by `status`, `job_type`, `entity_type`, and date range.
- Detect stuck jobs: `status = 'pending'` with `run_at` more than 10 minutes in the past.
- Detect expired leases: `status = 'running'` with `lease_expires_at < now()`.
- Show worker claim assignments: which `claimed_by` worker ID holds each running job.
- Allow operator to cancel a specific stuck pending job from the queue view.

### F. Campaign monitoring
- Show lead counts segmented by `campaign_name` and `lead_stage`.
- Show tier distribution: count of leads at each `ai_campaign_value` (None / 0 / 1 / 2 / 3) per campaign.
- Show conversion funnel: leads entered → voicemails → answers → intents detected → enrolled.
- Accept date range and campaign filter.
- Funnel numbers must be derived from `call_events`, `classification_results`, and `lead_state` only.

### G. AI performance tracking
- Show blank transcript rate: `call_events` where `transcript IS NULL OR transcript = ''` / total calls in range.
- Show unknown intent rate: `call_events` where `detected_intent = 'low_confidence_audio'` / total calls with intent.
- Show intent distribution: count per `detected_intent` value from `call_events.detected_intent`.
- Show consent decision distribution from `summary_results.summary_consent`.
- Show intent → outcome mapping: each intent with its count, share of total calls, and booking rate (only `enrolled` = 100%; all others = 0%).
- Show weekly AI quality trends: blank transcript rate and unknown intent rate per calendar week, via `GET /dashboard/ai-timeseries`.
- **Must NOT show** business KPIs (pickup rate, total calls, enrollment counts) — those belong to Voice Performance and Conversion Funnel pages.

### J. Sales Queue (`/conversion-funnel`)

> **Note (2026-04-10)**: This page was originally a funnel analysis view. It has been repurposed as a real-time sales action queue. The funnel analysis requirement is removed.

**Purpose**: Surface leads with high-intent calls that need immediate human follow-up.

**Data source**: `GET /dashboard/recent-calls` — calls with duration ≥ 30s, transcript, and recording URL.

**Display**:
- Calls split into two tables: **Active Queue** (non-terminal outcomes) and **Completed this session** (terminal outcomes: booked / not_interested / wrong_number).
- Default sort: priority DESC → score DESC → last_call_minutes_ago ASC.
- Priority badges: 🔴 urgent / 🟡 review / ⚪ none.
- Row highlight: urgent + `last_call_minutes_ago > 15` → red tint background.
- Columns: Priority | Name | Phone | Intent | Last Call | Score | Recording | Transcript | Action.
- **Name**: resolved using three-tier logic (see `GET /dashboard/recent-calls`). A phone-number value is never displayed as a name — `"Unknown"` is shown instead.
- **Call Now** button: opens the inline `OutcomeForm` with `callback_scheduled` pre-selected.
- **Mark Done** button: opens the inline `OutcomeForm` with `no_answer` pre-selected.
- **CSV export**: all enriched fields including full transcript.
- Topbar: urgent count badge + CSV download button.

**Outcome form**: submits to `POST /dashboard/sales-queue/outcome`. On success, moves the row from Active Queue to Completed.

**Lead name resolution rules**:
1. `raw_payload_json->>'Name'` — used unless the value matches a phone pattern or is blank.
2. `executed_actions->'get_the_user_preferences_from_gohighlevel'` GHL contact `firstName` — used for Inbound calls where `Name` is absent or is a phone number.
3. `"Unknown"` — final fallback.

### H. CRM sync health
- Show GHL write success rate per write path (Path 1 / Path 2 / Path 3) derived from `task_events` and `scheduled_jobs`.
- Show retry rate for `create_crm_task` and `update_ghl_after_vm_message` job types.
- Show failure classification from `exceptions` where type in (`crm_task_failed`, `ghl_vm_message_update_failed`, `student_summary_delivery_failed`).
- Shadow mode: show GHL shadow write count from `shadow_actions WHERE action_type = 'ghl_contact_update'`.

### I. Business KPIs (surfaced via Voice Performance page)
- Pickup rate, voicemail rate, failed rate, enrollment count — shown on Voice Performance page (`GET /dashboard/metrics`).
- All KPIs support date range and campaign filter.
- Standalone `/kpis` page **removed** (2026-04-06) — KPI data is now distributed: conversion metrics on Conversion Funnel page, AI metrics on AI Performance page, outcome trends on Voice Performance page.

### K. Lead journey (enhanced)
- Reuse existing Postgres query logic from the Streamlit Lead Journey.
- Improve visualization with per-step duration bars, intent signal badges, and AI output expansion.
- Show shadow-generated messages with clear shadow badge.
- Show pending next action with scheduled time and active-window deferral status.

## Non-functional requirements
- All Postgres queries must complete within 3 seconds under normal load (1000+ leads).
- The API must be stateless — no session state in process memory beyond the authenticated session token.
- Worker health polling must not add significant load; poll interval minimum 5 seconds.
- Email alerts must be delivered within 60 seconds of threshold breach detection.
- All API routes must return JSON. No server-side rendering of HTML from the API layer.
- The system must run in both `development` and `production` `APP_ENV` modes.
- In `development`, `SMTP_ENABLED=false` suppresses real email sends and logs to stdout instead.
