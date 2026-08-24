# spec/06_architecture.md

## Overview
Hybrid Python architecture with API layer + background workers.

## Components
1. API service: webhook intake (`POST /v1/webhooks/calls`), dashboard exception APIs, test call route (dev/staging only).
2. Worker service: AI jobs, CRM writes, retries, delays, outbound call launch, nurture scheduler, SMS/email channel delivery.
3. Postgres: authoritative store for state, jobs, audit, exceptions, outbound/inbound messages, shadow actions.
4. Redis + RQ: queue and job execution (three queues: `default`, `ai`, `callbacks`).
5. GHL adapter: contacts, notes, fields, tasks; reads always active; all writes shadow-gated. Three write paths: (1) completed-call AI analysis + task, (2) VM-tier SMS/email follow-up, (3) campaign finalization.
6. Synthflow adapter: outbound call launch for all campaigns (`launch_new_lead_call()`); voicemail tier callback scheduling via `schedule_callback()`.
7. OpenAI service: transcript analysis, summary generation, consent detection, AI-generated SMS/email bodies, GHL call analysis (`generate_ghl_call_analysis()`).
8. Shadow mode: when `SHADOW_MODE_ENABLED=true`, outbound calls are intercepted and logged to `shadow_actions` instead of executed. SMS and email content is **fully generated** by the AI pipeline and stored to `outbound_messages` with `status='shadow'` so the content is visible in Lead Journey. A `shadow_actions` row is also written with the full message payload. GHL writes are intercepted by the GHL adapter; after each VM-tier GHL write, a `shadow_actions` row with `action_type='ghl_contact_update'` is written with the human-readable field map so operators can inspect what would have been written to GHL. Active-window checks are enforced in shadow mode — scheduling behaviour matches production exactly.
9. Knowledge base: CSV file at `app/prompts/knowledge_base/video_transcripts.csv`; loaded by `load_video_transcripts()` with `@lru_cache`; used as story context for AI-generated SMS/email messages.
10. Streamlit dashboard (`execution/dashboard.py`): read-only monitoring UI backed by Postgres; 8 sections including Lead Journey with per-lead timeline, next-action previews, and SMS/email content preview.

## Live-call intent routing layer

Completed (answered) calls run through a second intent-detection pass after AI analysis, in addition to the existing voicemail-transcript intent detection. The detection uses three inputs:

- Transcript text (rule-based keyword patterns)
- `executed_actions` from the Synthflow payload (transfer attempts, booking success/failure)
- Call `duration_seconds` (used for `partial_engagement` short-call detection)

Four new signals extend `detect_intent()` in `app/core/intent_detection.py`:

| Signal | Trigger | Handler action |
|---|---|---|
| `human_transfer_request` | "talk to a real person" keywords or `executed_actions` transfer | status → `human_transfer`; +2 h follow-up if unconfirmed, capped at `HUMAN_TRANSFER_RETRY_CAP` (2) |
| `failed_booking` | "didn't work" / "couldn't book" keywords or `executed_actions` booking fail | +4 h retry, same campaign, capped at `FAILED_BOOKING_RETRY_CAP` (2) |
| `partial_engagement` | No strong intent + short call (< 120 s) | lifecycle → `cold`; **no retry** |
| `low_confidence_audio` | Transcript < 5 chars or noise-only | lifecycle → `cold`; **no retry** |

Two new lifecycle events are defined in `app/core/lifecycle.py`:
- `human_transfer`: any non-terminal → `"human_transfer"` status
- `low_confidence`: any non-terminal → `"cold"` status

**`partial_engagement` and `low_confidence_audio` never schedule another call or enter a new
campaign** (as of 2026-08-24) — both reached this condition from a genuinely deployed bug: a
number that produces one of these signals will reliably keep producing it (a business IVR, wrong
number, or disconnected line most commonly), so any retry cadence — even a capped one — just
delays the same infinite loop, which is exactly what happened in production (see PROGRESS.md
2026-08-24, `+18666932332` called every ~15 min for months). Both handlers now do nothing but
`transition_lead_state(lead, "low_confidence")` and stop; re-entry into a campaign, if warranted,
is a human decision or a fresh external GHL trigger.

**`failed_booking` and unconfirmed `human_transfer_request` had no cap at all** (found in the same
audit) — a production query turned up one contact with 83 accumulated retries across these two
reasons plus the callback-family intents since April (mostly the same self-call phone-mismapping
bug surfacing through a different code path — see PROGRESS.md 2026-08-24). Unlike
`partial_engagement`/`low_confidence_audio`, these two intents carry real signal (a genuine
booking or transfer attempt happened), so retrying isn't wrong in principle — it just needed a
ceiling. Both now retry up to their cap (`FAILED_BOOKING_RETRY_CAP`/`HUMAN_TRANSFER_RETRY_CAP`,
2 each, tracked via `_count_retries_by_reason()` against `scheduled_jobs.payload_json->>'intent_reason'`),
then fall through to `_mark_cold_no_retry()` — same terminal action as `partial_engagement`/
`low_confidence_audio` — instead of looping forever. `callback_request`/`callback_with_time`/
`call_later_no_time` were deliberately left uncapped — those are explicit lead asks ("call me
back"), and a cap would break the legitimate case; the residual risk there is intent
misclassification, not the retry policy itself.

**`interested_not_now` and `uncertain` are campaign-scoped** (as of 2026-08-24) — they normally
move the lead to `status="nurture"` with `next_action_at` set 2 days / 1 day out in production
(`NURTURE_DELAY_DAYS` env override; code default 7 / 3.5), and `nurture_scheduler.py` later
graduates the lead back into the Cold Lead campaign from a fresh tier 0 once that time passes.
For a New Lead contact this is a legitimate one-time downgrade (New Lead → wait → Cold Lead).
**But if the lead is already in Cold Lead or Inbound when either intent fires, there is no
legitimate downgrade to make** — Cold Lead is already the bottom outreach tier (nurture-then-retry
had no cap and could repeat every 1-2 days indefinitely; GHL separately re-registers exited Cold
Leads after ~2 months, so an uncapped Cora-side loop on top of that is redundant), and an Inbound
contact called *us* — auto-enrolling them into an outbound campaign from one ambiguous answer
isn't something they asked for. `_handle_interested_not_now` and `_handle_uncertain` now check
`_blocks_nurture_retry(lead)` first (true for `campaign_name` in `{"cold lead", "inbound"}`); if
true, they call the same `_mark_cold_no_retry()` helper `partial_engagement`/`low_confidence_audio`
use — mark cold, no retry, stop — instead of nurturing.

All handlers not covered above schedule a single one-shot retry call, uncapped by design
(explicit lead request) or governed by the tier-3-terminal voicemail engine for subsequent
progression.

Handlers live in `app/core/intent_actions.py`. Live-call routing is wired into `app/worker/jobs/ai_jobs.py` post-analysis, and `app/worker/jobs/voicemail_jobs.py` intent detection is extended to pass `executed_actions` and `duration_seconds`.

## Auto webhook recovery

`app/worker/jobs/webhook_recovery_jobs.py` — `auto_webhook_recovery_job` — runs every 5 minutes (self-rescheduling, same pattern as the nurture scheduler and slot rebalancer). On each run it:

1. Queries `scheduled_jobs` for `launch_outbound_call` jobs that completed in the last 24 hours but have no matching `call_events` row and no prior recovery/advance audit entry — same dataset as the Webhook Delivery panel.
2. For each failure (cap: 10 per cycle, oldest-first):
   - Looks up the campaign's Synthflow `model_id` from `_CAMPAIGN_MODEL_IDS` (Cold Lead, New Lead, Inbound).
   - Calls `SynthflowClient.list_calls()` with the contact phone and a 3-hour window around the job's execution time; paginates until a match is found or pages exhausted; picks the call record with `start_time` closest to the job's `run_at`.
   - **Terminal status found** (`completed`/`failed`/`hangup_on_voicemail`/`no_answer`/`left_voicemail`) → `recover_missed_webhook()` — schedules `process_call_event`, running the full AI + GHL pipeline exactly as if the webhook had arrived.
   - **Non-terminal status** (`in_progress`/`ringing`/etc.) → skip; recheck next cycle.
   - **No call found** → `advance_stale_lead(contact_id, "no_answer")` — retries the call or closes the lead per tier policy.

Fault tolerance: `StaleLeadConflict` (lead already has a pending job) is caught and logged as a skip. Individual errors do not stop the cycle. The job always self-reschedules in the `finally` block, even on failure.

Audit trail: `recover_missed_webhook()` writes `manual_webhook_recovery`; `advance_stale_lead()` writes `manual_advance`. Both are already in the Webhook Delivery panel's exclusion filter — recovered leads disappear from the panel automatically without dashboard changes.

Started automatically at `worker-default` boot via `start_webhook_recovery_scheduler()`.

## Nurture scheduler

`app/worker/jobs/nurture_scheduler.py` runs every 5 minutes (self-rescheduling). On each run it:
1. Queries `lead_state` for `status='nurture'` rows where `next_action_at <= now` (excludes `do_not_call`, `invalid`).
2. Applies `transition_lead_state(lead, "timeout")` → status `"cold"`.
3. Calls `enter_campaign(lead, "cold_lead")` — cancels pending jobs, resets tier, schedules first outbound call.

Fault tolerance: individual lead errors are caught and logged; the scheduler always self-reschedules even on fatal failure. Batch size capped at 50 rows per run. First job created by `ensure_scheduled()` on worker startup.

## Webhook payload normalisation

`normalize_synthflow_payload()` in `app/api/routes/webhooks.py` runs at webhook entry before any routing logic. Rules (additive — original fields preserved):

- **call_id**: resolved from `call_id` → `Call_id` → `callId` (first truthy value wins).
- **duration_seconds**: aliased from `duration` when `duration_seconds` absent.
- **direction**: defaults to `"outbound"` when absent.
- **phones**: if nested `phones.callee` / `phones.caller` present, extracted to `phone_number` / `phone_number_from`.
- **contact_id**: derived from `phone_number_to` → `phone` → `phone_number` when not present.
- **campaign_name**: inferred from `Agent` field (case-insensitive keyword match):
  - contains `coldlead` or `cold lead` → `"Cold Lead"`
  - contains `newlead` or `new lead` → `"New Lead"`
  - no keyword → preserve payload value; absent → default `"New Lead"`.
  - Rationale: Synthflow sends `campaign_name = "New Lead"` for all workflows. The `Agent` field uniquely identifies which Synthflow workflow ran the call and is the authoritative campaign signal.

## Campaign switching

`app/core/campaigns.py` provides two entry points:

- `evaluate_campaign_switch(campaign_name, intent)` — pure function, returns new campaign name or `None`.
- `apply_campaign_switch(session, lead, new_campaign_name, reason=...)` — lightweight field update; does NOT reset tier or cancel jobs. Writes one `audit_log` row (action=`campaign_switch`, operator_id=`system`) on success. Version conflicts produce no log row.

Switch rules:
- New Lead + `interested_not_now` or `uncertain` → Cold Lead
- Cold Lead + `re_engaged` → New Lead

Voicemail-sequence guard (enforced in `ai_jobs.py`): downgrade switches (New Lead → Cold Lead) are skipped when `lead_state.ai_campaign_value` is not `None` and not terminal `"3"`. Upgrade switches (Cold Lead → New Lead) are always applied.

Applied in `ai_jobs.py` after `handle_intent()`.

## SMS/email reply handling

SMS and email replies are handled entirely within GHL automations. This system does not receive inbound SMS/email reply webhooks, does not maintain an `inbound_messages` table for this purpose, and has no reply-suppression gate in `send_sms_job` or `send_email_job`. The `POST /v1/messages/inbound` endpoint has been removed.

Opt-out signals are handled at the **voice call** level only — if a lead says "stop calling", "unsubscribe", "not interested", etc. during a call, intent detection fires and:
- `do_not_call` → `lead_state.do_not_call = True`, `status = closed`, `AI Campaign = No` written to GHL
- `not_interested` → `status = closed`, `AI Campaign = No` written to GHL
- `wrong_number` → `lead_state.invalid = True`, `status = closed`, `AI Campaign = No` written to GHL

All three write `AI Campaign = No` via `_write_ghl_campaign_off()` in `app/core/intent_actions.py`, which stops GHL automations for the contact. This is the same pattern used by `enrolled` and `_finalize_campaign`.

## lead_state normalised_phone guarantee

`update_lead_state` (lifecycle_jobs.py) — when creating a new `lead_state` row for a contact whose first interaction was a completed (answered) call, derives `normalized_phone` from:
1. `call_event.raw_payload_json['phone_number_to']`
2. `call_event.raw_payload_json['phone_number']`
3. `call_event.raw_payload_json['phone']`
4. `contact_id` itself if it starts with `+` (phone-derived contact IDs)

This ensures Lead Journey phone-number lookup works for all leads regardless of which path (voicemail or call-through) created their `lead_state` row.

## Shadow mode loop behaviour

When `SHADOW_MODE_ENABLED=true`, `launch_outbound_call_job` intercepts the Synthflow call and logs to `shadow_actions` without placing a real call. Because no real call is placed, Synthflow never sends a completion webhook. The voicemail tier loop therefore does not advance beyond the intercepted step. Leads remain at their current `ai_campaign_value` with no further pending jobs until shadow mode is disabled and a real call completes.

## Key trade-offs
- Postgres chosen by business requirement despite earlier Postgres drafts.
- Redis/RQ chosen for simple delayed jobs while keeping canonical state in Postgres.
- GHL remains CRM authority while Postgres is campaign/process authority.
- Summary generation remains in scope because final source docs require it.
- One shared tier engine with per-campaign policies reduces complexity while preserving campaign flexibility.

## Risks and mitigations
- Duplicate replays -> dedupe keys.
- Lost delayed jobs -> canonical scheduled_jobs in Postgres.
- GHL auth/key drift -> critical alerting.
- Prompt regressions -> versioned prompt registry and shadow evaluation.
- Shadow/data drift between Sheets and DB -> reconciliation jobs and dashboards.

## Reporting architecture note
The reporting/dashboard layer shall read from Postgres-derived reporting tables or views.
Google Sheets shadow data may be mirrored for reconciliation, but reporting must not depend on Google Sheets at runtime.
Interactive behavior in the current phase is limited to filtering and cross-filtering; drill-down and KPI tooltips are deferred.