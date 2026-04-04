# spec/08_data_model.md

## lead_state
One row per contact. Updated in-place with optimistic concurrency (`version`).
- id
- contact_id — GHL contact ID or phone number when no GHL ID is available
- normalized_phone — E.164 phone; populated by voicemail path and (since 2026-03-31) by `update_lead_state` on row creation
- lead_stage — AI-classified stage from latest classification result
- campaign_name — "New Lead" | "Cold Lead"
- ai_campaign — GHL AI campaign field value
- ai_campaign_value — voicemail tier: None | "0" | "1" | "2" | "3" (terminal)
- last_call_status — status of the most recent processed call_event
- status — active | nurture | closed | human_transfer
- do_not_call — boolean; suppresses all outbound actions
- invalid — boolean; suppresses all outbound actions
- preferred_channel — "sms" | "email" | None (default: voice)
- next_action_at — nurture scheduler target; set by intent handlers
- last_replied_at — set when inbound SMS/email reply received; suppresses messaging
- version — incremented on every update; guards optimistic concurrency
- created_at
- updated_at

## call_events
One row per (call_id, action_type) dedupe_key. Idempotent under replay.
- id
- call_id — Synthflow call ID; primary correlation key
- contact_id — GHL contact ID or phone number; may be null until enrichment
- direction — "outbound" | "inbound"
- status — completed | voicemail | hangup_on_voicemail | left_voicemail | voicemail_detected | machine_detected | failed | in-progress | queue
- end_call_reason
- transcript
- duration_seconds
- recording_url
- start_time_utc
- model_id — Synthflow assistant/model ID
- lead_name
- agent_phone_number
- timeline — Synthflow conversation timeline JSON
- telephony_duration
- telephony_start
- telephony_end
- detected_intent — rule-based intent written after AI analysis (e.g. "re_engaged", "enrolled")
- dedupe_key — "{call_id}:process_call_event"
- raw_payload_json — full original Synthflow webhook payload; used for replay and Agent-field inference
- created_at

## classification_results
One row per (call_event_id, prompt_family) — multiple rows may exist for the same call.
- id
- call_event_id
- model_used
- prompt_family — `"lead_stage_classifier"` (from `run_call_analysis`) or `"ghl_call_analysis"` (from `create_crm_task`)
- prompt_version
- output_json — schema varies by prompt_family:
  - `lead_stage_classifier`: `{lead_stage, confidence, reasoning, ...}`
  - `ghl_call_analysis`: `{lead_classification, is_lead, ai_campaign, call_detailed_summary, task_title, assign_to, call_start_time, task_due_date}`
- created_at

Dashboard timeline joins on `prompt_family = 'ghl_call_analysis'` to display `lead_classification` and `call_detailed_summary` in the call event row.

## summary_results
- id
- call_event_id
- student_summary
- summary_offered
- summary_consent — "YES" | "NO" | "UNKNOWN"
- model_used
- prompt_family
- prompt_version
- created_at

## task_events
- id
- call_event_id
- provider_task_id
- status
- created_at

## scheduled_jobs
Canonical durable job record. Postgres is authoritative; Redis/RQ holds execution handles only.
- id
- job_type — process_call_event | process_voicemail_tier | run_call_analysis | launch_outbound_call | send_sms | send_email | create_crm_task | send_student_summary | update_lead_state | run_nurture_scheduler
- entity_type — call | lead
- entity_id — call_id or contact_id depending on job_type
- run_at
- rq_job_id
- status — pending | claimed | running | completed | failed | cancelled
- claimed_by — worker ID
- claimed_at
- lease_expires_at
- payload_json — job-specific context; always includes contact_id where applicable
- version — incremented on every status transition; guards atomic claim
- created_at
- updated_at

## exceptions
- id
- type — call_processing_failed | call_analysis_failed | lead_state_update_failed | unknown_call_status | call_pending | …
- severity — warning | critical
- status — open | resolved | ignored
- context_json
- entity_type
- entity_id
- created_at
- updated_at

## audit_log
Append-only; never updated or deleted.
- id
- entity_type — lead | call | exception | scheduled_job
- entity_id
- action — campaign_switch | retry_now | retry_delay | cancel_future_jobs | force_finalize | resolve | ignore
- operator_id — "system" for automated writes (campaign switches); operator ID string for dashboard actions
- context_json — action-specific detail (e.g. from/to/reason for campaign_switch)
- created_at

## outbound_messages
One row per send attempt. Written by send_sms_job / send_email_job (NOT written in shadow mode).
- id
- contact_id — GHL contact ID or phone number; dashboard queries include `OR contact_id = :phone` fallback for phone-as-ID leads
- channel — "sms" | "email"
- subject — email subject line (email only); null for SMS
- body — full message body (AI-generated via `generate_vm_followup()`)
- status — pending | sent | failed
- created_at

## inbound_messages
Written when an inbound SMS or email reply is received; triggers reply detection gate.
- id
- contact_id
- channel
- body
- created_at

## shadow_actions
Written when shadow mode intercepts an outbound action. Not written when shadow mode is off.
- id
- contact_id
- action_type — outbound_call | sms | email
- payload — action-specific context
- created_at

## Reporting entities or views (Postgres-derived)
- `fact_call_activity` — one row per call_event; joins lead_state for campaign context
- `fact_kpi_daily` — pre-aggregated daily KPI metrics
- `dim_date`, `dim_call_type`, `dim_campaign` — dimension tables for BI layer

## Reporting source-of-truth rule
Dashboard metrics shall be computed from Postgres-authoritative reporting views or tables.
Google Sheets is not used as a reporting source.
