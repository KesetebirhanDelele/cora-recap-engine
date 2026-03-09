# spec/00_overview.md

## Product one-liner
Cora Outbound Recap Engine is a Python-based API + worker platform that replaces the current Zapier workflows for inbound recap, outbound cold-lead recap, and outbound new-lead recap with durable Postgres-backed state, Redis/RQ job execution, GHL CRM updates, Synthflow callback scheduling, OpenAI-generated summaries, and consent-gated recap writeback.

## Target users
- **Admissions / Sales Reps**: receive CRM tasks for completed calls and continue human follow-up.
- **Admissions / Sales Managers**: monitor lead progression, voicemail recovery, and exception handling.
- **Ops/Admin**: manage retries, replay stuck events, inspect state, and resolve failures.
- **Engineering**: operate API, workers, Redis/RQ, Postgres, and integration health.
- **Students / Leads**: receive calls and have recap content stored to CRM only when consent is detected.

## Top 3 user journeys
### 1. Completed call through path
1. A call event arrives with `call_id`, phones, status, and timing.
2. The system enriches the event from GHL/LeadConnector and canonical call-analysis source.
3. The system upserts an idempotent call record keyed by `call_id`.
4. The system updates CRM fields and creates one GHL task for each completed non-voicemail call.
5. The system generates a student summary and consent decision.
6. If consent is `YES`, the system writes the student summary back to the configured GHL recap field.

### 2. Unified voicemail recovery path
1. A voicemail or voicemail-hangup outcome is detected.
2. The system routes by campaign type and canonical tier state using `None -> 0 -> 1 -> 2 -> 3 (terminal)`.
3. The system uses one shared tier engine, but applies campaign-specific policy for delays, actions, and finalization writes.
4. Cold Lead policy uses: None→0 = 2 hours, 0→1 = 2 days, 1→2 = 2 days, 2→3 = finalize with no Synthflow callback.
5. New Lead policy may use different timing/actions while preserving the same tier numbering model.
6. Tier 3 turns off AI campaign activity in GHL and ends automated callback progression.

### 3. Pending/stuck recovery via dashboard
1. A call remains `queue` or `in-progress`, or a critical dependency fails.
2. The worker retries within bounded policy.
3. If the event cannot complete safely, an exception is stored in Postgres.
4. The admin dashboard exposes the exception, state inspection, retry controls, cancel-future-jobs, and force-finalize actions.

## System boundary
### In scope
- Python API service for webhook intake and admin operations.
- Python worker service for retries, delayed jobs, callbacks, AI jobs, and CRM writes.
- Postgres as authoritative store for campaign state, call events, audit, exceptions, and scheduled-job records.
- Redis + RQ for job execution.
- GHL / LeadConnector for contact lookup, custom-field writes, notes, and task creation.
- Synthflow for callback creation on eligible voicemail tiers.
- OpenAI for completed-call analysis, student summary generation, consent detection, and voicemail content generation.
- Postgres-authoritative reporting dataset and dashboard support
- dashboard filtering and cross-filtering across supported visuals
- Google Sheets mirror in shadow mode, with sheet data mirrored into Postgres during cutover.


### Out of scope
- Replacing GHL as CRM.
- Direct SMS/email sending inside this app; the app writes fields and state used by GHL automations.
- Building a custom dialer in place of Synthflow.
- Regulated health-data workflows.
- dashboard drill-down interactions in the current phase
- KPI tooltip interactions in the current phase

## Success metrics
1. Appointment / enrollment conversion rate from inbound and outbound leads improves versus current baseline.
2. Callback completion rate after voicemail tiers improves versus current baseline.
3. Duplicate-action rate for `call_id` remains near zero.
4. Task creation accuracy for completed non-voicemail calls stays at or above 99%.
5. Summary writeback occurs only when consent is `YES` and valid content exists.
6. Critical failures are visible in the dashboard within 1 minute of exception creation.

## Assumptions
- `New Lead` and `Cold Lead` remain the only in-scope lead-stage values for outbound workflows.
- No additional lead classification mapping is required beyond current workflow outputs.
- Indefinite retention is required for transcripts, AI outputs, and audit metadata.
- GHL is authenticated using per-location API keys.

