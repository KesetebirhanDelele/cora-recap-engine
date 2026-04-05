# spec/17_dashboard_guide.md — Dashboard Usage Guide

## Overview

The Cora Recap Engine monitoring dashboard (`execution/dashboard.py`) is a read-only Streamlit application that provides full observability into the system's operational state. It connects directly to Postgres and never writes to any external system — the only writes it performs are to `audit_log` for dashboard-initiated operator actions (Exceptions section).

### How to run

```bash
# From project root, with .env loaded and DB reachable:
streamlit run execution/dashboard.py
```

Requires: `streamlit`, `pandas`, `sqlalchemy`, `psycopg2-binary` all installed.

### Sidebar

The sidebar shows three always-visible status indicators:
- **Env** — current `APP_ENV` value (`development` / `production`)
- **Shadow mode** — 🟡 ON means all outbound calls/SMS/email are intercepted and logged, not sent
- **GHL writes** — `shadow` means all GHL contact/task writes are intercepted (no live CRM changes)

Use these indicators to confirm you are reading production state before taking any operator action.

---

## Section-by-section guide

### 1. Overview

**Purpose**: High-level health summary — counts of active leads, open exceptions, pending jobs, and recent call volume.

**How to interpret**:
- **Active leads** — leads where `lead_state.status` is not `closed` or `do_not_call`. A large number is expected during active campaigns.
- **Open exceptions** — unresolved job failures requiring operator attention. Any number above zero warrants review in the Exceptions section.
- **Pending scheduled jobs** — jobs queued but not yet claimed. High counts during off-hours are normal; high counts during active hours may indicate worker saturation.
- **Calls today / this week** — call volume from `call_events`. Drops may indicate Synthflow webhook routing issues.

---

### 2. Campaign Overview

**Purpose**: Summarize lead counts by campaign and lead stage, with optional date filtering.

**How to use**:
- Use the **date range picker** to filter to a specific period.
- The table shows lead counts segmented by `campaign_name` ("New Lead" / "Cold Lead") and `lead_stage` (AI-classified stage from the most recent call analysis).
- Use this to track funnel progression: how many cold leads have been classified as "interested", "enrolled", etc.

**How to interpret**:
- A high number of leads in `None` stage indicates calls that completed without triggering AI analysis (blank transcripts, shadow mode interception before AI, or analysis failures).
- `enrolled` counts are the primary success signal.
- `do_not_call` / `wrong_number` counts indicate data quality issues worth reviewing.

---

### 3. Trends

**Purpose**: Time-series charts of call volume, voicemail rate, and exception rate.

**How to use**:
- Use the **date range** and **campaign filter** controls to scope the view.
- Charts show daily aggregates.

**How to interpret**:
- **Voicemail rate**: Rising voicemail rate without a corresponding rise in callback completions suggests tire saturation — leads may need a different approach.
- **Exception rate**: Spikes typically correlate with API failures (Synthflow, GHL, or OpenAI). Cross-reference with the Exceptions section for the specific failure type.
- **Call volume gaps**: Days with zero calls may indicate worker downtime, Redis disconnects, or Synthflow configuration changes.

---

### 4. Recent Calls

**Purpose**: Raw feed of the most recent `call_events` rows, newest first.

**How to use**:
- Review the table to confirm recent calls are being ingested and processed.
- `contact_id`, `call_id`, `status`, `duration_seconds`, `campaign_name`, and `detected_intent` are all shown.

**How to interpret**:
- `status = "completed"` → call was answered; AI analysis + GHL write path should have run.
- `status` in `{voicemail, hangup_on_voicemail, left_voicemail, voicemail_detected, machine_detected}` → voicemail tier engine handled the call; SMS/email follow-up may be pending.
- `detected_intent` blank → intent detection did not fire (blank transcript, or intent was `None` — no strong signal).
- `duration_seconds = 0` with `status = completed` → likely a hangup before conversation; AI analysis ran but may have produced low-confidence output.

---

### 5. Lead State

**Purpose**: View the current `lead_state` table — one row per contact.

**Columns of interest**:
- `campaign_name` — active campaign (New Lead / Cold Lead)
- `ai_campaign_value` — voicemail tier (None → 0 → 1 → 2 → 3). `3` = terminal; no further automated callbacks.
- `status` — `active` (normal), `nurture` (waiting for re-engagement window), `closed`, `human_transfer` (pending human follow-up), `do_not_call`
- `next_action_at` — when the nurture scheduler will re-engage this lead (only relevant when `status = nurture`)
- `last_replied_at` — not actively used; SMS/email reply handling is owned by GHL automations

**How to interpret**:
- Many leads stuck at `ai_campaign_value = None` → first call was a voicemail but no follow-up scheduled. Check Exceptions section.
- Many leads at `status = nurture` → working as intended if `NURTURE_DELAY_DAYS` is configured; check `next_action_at` to confirm re-engagement is scheduled soon.
- `do_not_call = true` → lead explicitly requested no contact. These are never dialed or messaged.

---

### 6. Shadow Actions

**Purpose**: Review all outbound actions (calls, SMS, email) that were intercepted by shadow mode instead of being executed.

**How to use**:
- Filter by `action_type` (`outbound_call` / `sms` / `email`) and date range.
- Each row shows the full `payload` of what would have been sent.

**How to interpret**:
- Shadow actions represent "what the system would have done" if shadow mode were off. Use this to validate that the correct messages and call schedules are being generated before going live.
- A shadow action row for `sms` or `email` contains the AI-generated message body — review for content quality.
- A shadow action for `outbound_call` contains the Synthflow payload — verify `model_id`, `phone_number`, and `campaign_name`.
- **Important**: SMS/email shadow-action rows appear here only when `SHADOW_MODE_ENABLED=true`. Separately, `outbound_messages` rows are written only when NOT in shadow mode (i.e., when messages are actually sent). The two tables are mutually exclusive per message.

---

### 7. Scheduled Jobs

**Purpose**: Inspect the `scheduled_jobs` table — the canonical durable job registry.

**How to use**:
- Filter by `status` (pending / claimed / running / completed / failed / cancelled) and `job_type`.
- Use this to investigate stuck or failed jobs.

**How to interpret**:
- `status = pending` with `run_at` in the past → job should have been claimed; if persisting, Redis/RQ may be disconnected.
- `status = failed` → job failed after retry exhaustion; check the `payload_json` for context and the Exceptions section for the corresponding exception row.
- `status = cancelled` → manually cancelled by an operator action or by campaign finalization.
- Multiple `pending` rows for the same `contact_id` and `job_type` → may indicate duplicate scheduling (investigate with `duplicate_action` count in Overview).

---

### 8. Exceptions

**Purpose**: Review and resolve open job failures.

**How to use**:
- The table shows all open exceptions by severity (`critical` / `warning`) and type.
- Click the expand arrow on any row to see the full `context_json`.
- Use the **action buttons** to:
  - **Retry now** — re-enqueue the failed job immediately
  - **Retry in N minutes** — schedule a delayed retry
  - **Cancel future jobs** — mark all pending jobs for this lead cancelled
  - **Force finalize** — skip remaining campaign steps and mark the lead finalized
  - **Resolve** — mark exception resolved (no re-run, just closes it)
  - **Ignore** — suppress the exception from the open list

**How to interpret**:
- `crm_task_failed` — GHL API failure during `create_crm_task`; safe to retry; not lead-blocking.
- `call_analysis_failed` — OpenAI failure; transcript analysis did not run; retry will re-run the full AI pipeline.
- `call_processing_failed` — core event processing failure; critical if transcript + lead state were not written; investigate `context_json` for root cause.
- `unknown_call_status` — Synthflow sent a `call_status` value the system does not recognize; review the normalizer or Synthflow config.
- `student_summary_delivery_failed` — GHL write failure for student summary; non-critical; retry restores.

All operator actions are written to `audit_log` with the operator ID from the `X-Operator-Id` request header.

---

### 9. Contact Drill-Down

**Purpose**: View all data for a single contact — call events, lead state, scheduled jobs, exceptions, and messages — in one place.

**How to use**:
- Enter a `contact_id` (GHL contact ID or phone number) in the search box.
- All related rows from every table are displayed.

**How to interpret**:
- Use this when a specific lead needs investigation — e.g. "Why did this lead not get a follow-up SMS?" or "Did the task get created for this call?"
- If `task_events` is empty for a `call_event_id` that has `status = completed`, the CRM task was either blocked by dedupe or the job failed — check Exceptions.
- If `outbound_messages` is empty but the lead has completed voicemail tiers → confirm shadow mode is not suppressing sends.

---

### 10. Lead Journey

**Purpose**: Chronological touchpoint history for a single lead, looked up by phone number. The primary diagnostic view for understanding what happened to a specific lead from first contact to current state.

**How to use**:
1. Enter the lead's **phone number** (E.164, e.g. `+12145551234`) in the search box.
2. The top section shows a **summary card**: lead name, campaign, voicemail tier, status, and current `lead_stage`.
3. The **Next action** section shows the next pending scheduled job and, if it is an SMS or email, buttons to preview the AI-generated message content.
4. The **Timeline** section shows a unified chronological feed of all touchpoints.

#### Summary card

| Field | Meaning |
|---|---|
| Campaign | Current active campaign (New Lead / Cold Lead) |
| VM tier | Current voicemail tier (None / 0 / 1 / 2 / 3). `3` = finalized |
| Status | `active` / `nurture` / `closed` / `human_transfer` / `do_not_call` |
| Lead stage | Latest AI-classified lead stage from `classification_results` |

#### Next action block

Shows the next pending `scheduled_job` for this lead. If the job is `send_sms` or `send_email`:
- **Generate SMS/Email preview** button appears.
- Clicking it calls the AI generator (`generate_vm_followup`) using the current `attempt_number` from the job payload and the lead's context.
- The generated content is cached in `st.session_state` — subsequent reruns do not re-call OpenAI.
- For email: subject, plain-text preview, and full HTML body are shown.
- For SMS: the message text is shown directly.

This preview shows exactly what the lead will receive when the job runs — use it to validate content quality before going live.

#### Timeline events

Each event type has a distinct appearance:

**Call event** (📞):
- Header shows: call date/time, campaign name, duration, status, and (if available) `lead_classification` badge (🏷)
- If `call_detailed_summary` is available (from `ghl_call_analysis`), it is shown as "AI Call Summary" before the transcript
- Full transcript is shown in an expander
- If `lead_classification` is blank, AI call analysis either did not run or is not yet persisted (check if `create_crm_task` job completed)

**SMS/Email event** (📱/📧 or 💬 🔮 *shadow*):
- Shows channel, date/time, and message content
- For email: subject is shown above the body; full HTML body is rendered with `unsafe_allow_html=True`
- For SMS: plain text body is shown
- Messages from live mode have no badge. Messages generated in shadow mode display a 🔮 *shadow* badge and a note inside the expander — they were generated but not sent to the lead.
- `outbound_messages` rows exist for both live (`status='pending'/'sent'/'failed'`) and shadow (`status='shadow'`) sends. No longer necessary to check Shadow Actions to see message content.

**GHL Update (Shadow) event** (📋):
- Appears only in shadow mode when `update_ghl_after_vm_message` runs.
- Shows the exact GHL contact fields that would be written: Mark as Lead, Support Ticket #2, Message, AI Campaign, Support Ticket #4.
- One row per SMS/email send that triggers a GHL Path 2 update.

**Campaign switch event** (🔄):
- Shows from → to campaign name and the reason (intent that triggered the switch)
- Written by `apply_campaign_switch()` to `audit_log`

**Lifecycle/status event** (📋):
- Operator actions from the Exceptions section — retries, cancellations, force-finalize

**Reading the timeline**:
- A gap between a call event and the next outbound message indicates the delay window (controlled by `SMS_FOLLOWUP_DELAY_MINUTES` and VM tier delays).
- A call event with no subsequent SMS/email may mean: (a) shadow mode suppressed the message, or (b) the send job failed (check Exceptions).
- Campaign switches appear as their own row between the events that triggered them.

---

### 11. Settings — Brand & Messaging

**Purpose**: View the active brand and messaging configuration used for AI-generated SMS/email content.

**Fields shown**:
- `brand_name` — brand name injected into messages
- `sender_name` — sender name for personalized SMS greetings
- `reply_to_email` — email reply-to address
- `unsubscribe_text` — footer unsubscribe copy
- `next_class_start` — next class start date injected into call-to-action messages
- `live_open_house_link` — open house registration link
- `explainer_open_house_video_link` — YouTube explainer video link

These are read-only in the dashboard. Values are configured in `.env` and loaded via `app/config/settings.py`.

---

## Common diagnostic workflows

### "Why didn't this lead get a follow-up SMS?"

1. Lead Journey → enter phone number
2. Check Next action — is a `send_sms` job pending?
3. If no pending job: check Timeline for `send_sms` completed event — did it run already?
4. If shadow mode is ON: check Shadow Actions — the SMS was intercepted there, not sent
5. If Exceptions section shows `send_sms_failed`: retry the job
6. Note: SMS/email reply handling is owned by GHL automations — replies do not reach this system and do not suppress sends

### "Why is there no AI summary / lead classification in the timeline?"

1. Lead Journey → find the call event in the timeline
2. If `call_detailed_summary` is missing: `create_crm_task` job may not have run yet, or it failed
3. Exceptions section → search for `crm_task_failed` for this contact
4. If exception exists → retry; after retry, `classification_results` row is written and timeline shows the data on next refresh
5. If no exception and no `task_events` row → check if the job was ever scheduled (Scheduled Jobs section, filter by `contact_id`)

### "Did the GHL task get created?"

1. Contact Drill-Down → enter `contact_id`
2. Look for `task_events` rows — `status = created` means the task was created (or shadow-logged)
3. `provider_task_id` will be null in shadow mode (GHL API was not called)
4. To confirm a live write happened: `GHL_WRITE_MODE` must be `live` and `provider_task_id` must be non-null

### "What will the next SMS say?"

1. Lead Journey → enter phone
2. Next action block → if job is `send_sms`, click "Generate SMS preview"
3. The generated text is shown; it uses the current knowledge base context and lead tier

---

## Notes for operators

- The dashboard is **read-only** except for Exceptions actions (retry, resolve, ignore, cancel, force-finalize). All such actions are audit-logged with your operator ID.
- Shadow mode does not affect dashboard reads. You can see shadow actions and shadow outbound_messages even when the system is in full shadow mode.
- In shadow mode, SMS/email content IS generated and visible directly in the Lead Journey timeline (🔮 *shadow* badge). GHL field writes appear as 📋 GHL Update (Shadow) events immediately following each message event. No need to cross-reference the Shadow Actions section for message content inspection.
- All queries run against Postgres directly — no cache layer. Refresh the browser to get current state.
- The dashboard does not auto-refresh. Use the browser refresh button or Streamlit's `st.rerun()` (available in some sections after taking an action).
