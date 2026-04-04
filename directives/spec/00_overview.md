# spec/00_overview.md

## Product one-liner
Cora Outbound Recap Engine is a Python-based API + worker platform that replaces the current Zapier workflows for inbound recap, outbound cold-lead recap, and outbound new-lead recap with durable Postgres-backed state, Redis/RQ job execution, GHL CRM updates, Synthflow outbound call scheduling, OpenAI-generated summaries, and consent-gated recap writeback.

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
4. **GHL Path 1 — AI call analysis + CRM update**: `create_crm_task` fetches the GHL contact, runs `generate_ghl_call_analysis()` (OpenAI gpt-4o-mini), writes 5 contact fields (`Mark as Lead`, `AI Lead Assign To`, call summary ticket, `AI Lead Classification`, `AI Campaign`), creates a GHL task with `assigned_to` and `task_due_date`, and persists the analysis to `classification_results` (prompt_family=`ghl_call_analysis`).
5. The system generates a student summary and consent decision.
6. If consent is `YES`, the system writes the student summary back to the configured GHL recap field.
7. The system runs intent detection on the transcript (including `executed_actions` signals) and applies live-call routing: schedule follow-up, move to Cold Lead, or take no additional action.

### 2. Unified voicemail recovery path
1. A voicemail or voicemail-hangup outcome is detected.
2. The system routes by campaign type and canonical tier state using `None -> 0 -> 1 -> 2 -> 3 (terminal)`.
3. The system uses one shared tier engine, but applies campaign-specific policy for delays, actions, and finalization writes.
4. Cold Lead policy uses: None→0 = 2 hours, 0→1 = 2 days, 1→2 = 2 days, 2→3 = finalize with no Synthflow callback.
5. New Lead policy may use different timing/actions while preserving the same tier numbering model.
6. After each voicemail-triggered SMS or email, **GHL Path 2** runs: `update_ghl_after_vm_message` writes `Mark as Lead=Yes`, the brief message identifier (Support Ticket #2), the full message body (`Message` field), `AI Campaign=Yes`, and the latest lead classification (Support Ticket #4) to the GHL contact.
7. Tier 3 triggers **GHL Path 3** — finalization: `_finalize_campaign` writes `Mark as Lead=Yes` and `AI Campaign=No`, marking the end of automated follow-up in GHL.

### 3. Live-call intent routing
1. A completed (answered) call arrives.
2. After AI analysis, `detect_intent()` is called with the transcript, `executed_actions` flags, and call duration.
3. If a signal is detected, `handle_intent()` applies the appropriate lifecycle action:
   - `human_transfer_request` → set status `human_transfer`; schedule +2 h follow-up if transfer unconfirmed
   - `failed_booking` → keep campaign; schedule +4 h retry
   - `partial_engagement` → keep campaign; schedule +2 h retry (after 2 retries: escalate to Cold Lead)
   - `low_confidence_audio` → lifecycle transition to `cold`; enter Cold Lead campaign (resets tier, cancels jobs, schedules first call)
4. Campaign switch rules apply after intent routing (e.g. `re_engaged` on a Cold Lead → switch to New Lead).
5. Downstream AI jobs (`update_lead_state`, `create_crm_task`, `send_student_summary`) complete regardless of intent outcome.

### 4. Pending/stuck recovery via dashboard
1. A call remains `queue` or `in-progress`, or a critical dependency fails.
2. The worker retries within bounded policy.
3. If the event cannot complete safely, an exception is stored in Postgres.
4. The admin dashboard exposes the exception, state inspection, retry controls, cancel-future-jobs, and force-finalize actions.

## System boundary
### In scope
- Python API service for webhook intake and admin operations.
- Python worker service for retries, delayed jobs, AI jobs, CRM writes, outbound call scheduling, SMS/email channel delivery.
- Postgres as authoritative store for campaign state, call events, audit, exceptions, outbound/inbound messages, shadow actions, and scheduled-job records.
- Redis + RQ for job execution.
- GHL / LeadConnector for contact lookup, custom-field writes, notes, and task creation. Full integration contract: `spec/16_ghl_integration.md`.
- Synthflow for outbound call launch (voicemail tiers and campaign entry).
- OpenAI for completed-call analysis, student summary generation, consent detection, and AI-generated SMS/email bodies.
- Postgres-authoritative reporting dataset and Streamlit monitoring dashboard.
- Shadow mode: all GHL writes and outbound actions intercepted and logged to `shadow_actions` when `SHADOW_MODE_ENABLED=true`.
- Campaign switching: bidirectional New Lead ↔ Cold Lead based on detected intent.
- Lead Journey dashboard page: per-lead chronological touchpoint history, filterable by phone number.

### Out of scope
- Replacing GHL as CRM.
- Direct SMS/email sending as a primary channel; the app writes fields and state used by GHL automations, and sends AI-generated messages as secondary follow-up only.
- Building a custom dialer in place of Synthflow.
- Regulated health-data workflows.
- Google Sheets shadow sync (removed — user visually inspects Sheets; no programmatic mirror required).
- Dashboard drill-down and KPI tooltip interactions (deferred).

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
- The AI knowledge base for SMS/email generation is a CSV file at `app/prompts/knowledge_base/video_transcripts.csv` with columns: `video name`, `transcript`, `summary`, `platform`, `category`, `URL`. The loader is `@lru_cache`-backed and immutable at runtime.
- GHL reads (contact lookup) are always active regardless of write-mode settings. Only writes are shadow-gated.
- `classification_results` stores rows from two prompt families for the same call: `lead_stage_classifier` (from `run_call_analysis`) and `ghl_call_analysis` (from `create_crm_task`). Both coexist; the timeline joins specifically on `prompt_family='ghl_call_analysis'` for rich call-summary data.

