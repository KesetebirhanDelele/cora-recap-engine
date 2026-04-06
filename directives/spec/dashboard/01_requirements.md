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
- Show per-prompt-family metrics: call count, success rate, average latency (derived from `classification_results.created_at` minus parent `call_events.created_at`), blank transcript rate.
- Show intent distribution: count per detected_intent value from `call_events.detected_intent`.
- Show consent decision distribution from `summary_results.summary_consent`.
- Show fallback rate: rows in `classification_results` where `output_json` contains fallback indicators.

### H. CRM sync health
- Show GHL write success rate per write path (Path 1 / Path 2 / Path 3) derived from `task_events` and `scheduled_jobs`.
- Show retry rate for `create_crm_task` and `update_ghl_after_vm_message` job types.
- Show failure classification from `exceptions` where type in (`crm_task_failed`, `ghl_vm_message_update_failed`, `student_summary_delivery_failed`).
- Shadow mode: show GHL shadow write count from `shadow_actions WHERE action_type = 'ghl_contact_update'`.

### I. Business KPIs
- Pickup rate: `call_events` with `status = 'completed'` / total calls in range.
- Voicemail rate: `call_events` with voicemail status / total calls in range.
- Callback completion rate: leads that had a voicemail at tier > 0 and subsequently had a completed call.
- Campaign exit rate: leads that reached `ai_campaign_value = '3'` (terminal) / leads that entered.
- Do-not-call rate: `lead_state.do_not_call = true` count / total leads.
- All KPIs must support date range and campaign filter.

### J. Lead journey (enhanced)
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
