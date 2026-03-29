# spec/06_architecture.md

## Overview
Hybrid Python architecture with API layer + background workers.

## Components
1. API service: webhook intake, dashboard APIs, replay APIs.
2. Worker service: AI jobs, CRM writes, retries, delays, callback jobs, nurture scheduler, channel delivery.
3. Postgres: authoritative store for state, jobs, audit, exceptions, outbound/inbound messages, shadow actions.
4. Redis + RQ: queue and job execution.
5. GHL adapter: contacts, notes, fields, tasks.
6. Synthflow adapter: delayed callback creation (voicemail tiers), outbound call launch (new leads).
7. OpenAI service: transcript analysis, summary generation, consent detection, voicemail content, AI-generated SMS/email bodies.
8. Google Sheets mirror: active during shadow mode; data mirrored into Postgres for comparison and cutover tracking.

## Live-call intent routing layer

Completed (answered) calls run through a second intent-detection pass after AI analysis, in addition to the existing voicemail-transcript intent detection. The detection uses three inputs:

- Transcript text (rule-based keyword patterns)
- `executed_actions` from the Synthflow payload (transfer attempts, booking success/failure)
- Call `duration_seconds` (used for `partial_engagement` short-call detection)

Four new signals extend `detect_intent()` in `app/core/intent_detection.py`:

| Signal | Trigger | Handler action |
|---|---|---|
| `human_transfer_request` | "talk to a real person" keywords or `executed_actions` transfer | status → `human_transfer`; +2 h follow-up if unconfirmed |
| `failed_booking` | "didn't work" / "couldn't book" keywords or `executed_actions` booking fail | +4 h retry, same campaign |
| `partial_engagement` | No strong intent + short call (< 120 s) | +2 h retry, same campaign; after 2 retries → Cold Lead (same as `low_confidence_audio`) |
| `low_confidence_audio` | Transcript < 5 chars or noise-only | lifecycle → `cold`; enter Cold Lead campaign |

Two new lifecycle events are defined in `app/core/lifecycle.py`:
- `human_transfer`: any non-terminal → `"human_transfer"` status
- `low_confidence`: any non-terminal → `"cold"` status

`low_confidence_audio` and `partial_engagement` (after the retry cap) are the two handlers that enter a new campaign. All other handlers schedule a single one-shot retry call; the voicemail tier engine then governs further progression if subsequent calls go to voicemail.

`partial_engagement` retry cap: tracked via `scheduled_jobs` where `intent_reason='partial_engagement'` and `status != 'cancelled'`; cap constant `PARTIAL_ENGAGEMENT_RETRY_CAP = 2` in `app/core/intent_actions.py`.

Handlers live in `app/core/intent_actions.py`. Live-call routing is wired into `app/worker/jobs/ai_jobs.py` post-analysis, and `app/worker/jobs/voicemail_jobs.py` intent detection is extended to pass `executed_actions` and `duration_seconds`.

## Nurture scheduler

`app/worker/jobs/nurture_scheduler.py` runs every 5 minutes (self-rescheduling). On each run it:
1. Queries `lead_state` for `status='nurture'` rows where `next_action_at <= now` (excludes `do_not_call`, `invalid`).
2. Applies `transition_lead_state(lead, "timeout")` → status `"cold"`.
3. Calls `enter_campaign(lead, "cold_lead")` — cancels pending jobs, resets tier, schedules first outbound call.

Fault tolerance: individual lead errors are caught and logged; the scheduler always self-reschedules even on fatal failure. Batch size capped at 50 rows per run. First job created by `ensure_scheduled()` on worker startup.

## Campaign switching

`app/core/campaigns.py` provides two entry points:

- `evaluate_campaign_switch(campaign_name, intent)` — pure function, returns new campaign name or `None`.
- `apply_campaign_switch(session, lead, new_campaign_name, reason=...)` — lightweight field update; does NOT reset tier or cancel jobs.

Switch rules:
- New Lead + `interested_not_now` or `uncertain` → Cold Lead
- Cold Lead + `re_engaged` → New Lead

Applied in `ai_jobs.py` after `handle_intent()` and in `voicemail_jobs.py` via `process_voicemail_tier`.

## Reply detection and message suppression

`app/core/reply_detection.py` — `has_recent_reply(session, contact_id)` checks two signals:
1. `inbound_messages` table has any row for the contact.
2. `lead_state.last_replied_at` is not null.

Both `send_sms_job` and `send_email_job` call this gate immediately after claiming. If a reply is detected, the job completes silently (no message sent). Fail-open: DB errors return `False` so messaging is never suppressed due to a detection failure.

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