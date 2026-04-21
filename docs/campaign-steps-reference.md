# Campaign Steps Reference

All delays are configurable via the Settings page or `.env`. Defaults shown below.

---

## Table 1 — End-to-End Campaign Flow

Every inbound Synthflow webhook triggers this sequence regardless of campaign type.

| Step | Trigger | Job Type | What Happens | Output |
|---|---|---|---|---|
| 1 | Synthflow webhook received | `process_webhook` | Normalize payload; upsert `call_events` row keyed by `call_id` | `call_events` row |
| 2 | Webhook processed | `run_call_analysis` | OpenAI GPT-4o mini: classify lead stage, generate student summary, detect consent, detect intent | `classification_results` rows; intent result |
| 3 | Analysis complete | `create_crm_task` | **GHL Path 1** — write 5 contact fields + create GHL task with `assigned_to` and `task_due_date` | GHL task + field update (shadow-gated) |
| 4 | Analysis complete | `send_student_summary` | Consent-gated recap writeback to GHL recap field. Runs only when `consent = YES` | GHL field write (shadow-gated) |
| 5 | Analysis complete | `update_lead_state` | Persist AI campaign value, tier, and status to `lead_state` | `lead_state` row updated |
| 6 | Intent detected (answered call) | `handle_intent` | Route lead based on intent — see Table 3 | Varies by intent |
| 6 | Voicemail / hangup detected | `process_voicemail_tier` | Advance tier engine — see Table 2 | Next call + SMS ± email scheduled |

> Steps 3, 4, 5 run in parallel after step 2. Step 6 branches on call outcome.

---

## Table 2 — Voicemail Tier Engine

Applies to both campaigns. Tier advances each time the call is unanswered (voicemail, hangup on voicemail, machine detected).

### New Lead

| Tier Transition | Delay Before Next Call | Next Call Scheduled? | SMS Scheduled? | Email Scheduled? | GHL Write? |
|---|---|---|---|---|---|
| None → 0 (1st missed call) | Configurable (`new_vm_tier_none_delay_minutes`) | Yes — Synthflow callback | Yes — +30 min | No | GHL Path 2 after SMS |
| 0 → 1 (2nd missed call) | Configurable (`new_vm_tier_0_delay_minutes`) | Yes — Synthflow callback | Yes — +30 min | **Yes — same time as SMS** | GHL Path 2 after SMS/email |
| 1 → 2 (3rd missed call) | Configurable (`new_vm_tier_1_delay_minutes`) | Yes — Synthflow callback | Yes — +30 min | No | GHL Path 2 after SMS |
| 2 → 3 (4th missed call) | None — terminal | **No** | **No** | **No** | **GHL Path 3** — AI Campaign = No |

### Cold Lead

| Tier Transition | Delay Before Next Call | Next Call Scheduled? | SMS Scheduled? | Email Scheduled? | GHL Write? |
|---|---|---|---|---|---|
| None → 0 (1st missed call) | 2 hours (`cold_vm_tier_none_delay_minutes = 120`) | Yes — Synthflow callback | Yes — +30 min | No | GHL Path 2 after SMS |
| 0 → 1 (2nd missed call) | 48 hours (`cold_vm_tier_0_delay_minutes = 2880`) | Yes — Synthflow callback | Yes — +30 min | **Yes — same time as SMS** | GHL Path 2 after SMS/email |
| 1 → 2 (3rd missed call) | 48 hours (`cold_vm_tier_1_delay_minutes = 2880`) | Yes — Synthflow callback | Yes — +30 min | No | GHL Path 2 after SMS |
| 2 → 3 (4th missed call) | None — terminal | **No** | **No** | **No** | **GHL Path 3** — AI Campaign = No |

**SMS rule:** Always sent +30 min after every missed call (`sms_followup_delay_minutes = 30`).  
**Email rule:** Sent only on the 2nd missed call (tier 0 → 1), at the same scheduled time as the SMS.  
**GHL Path 2** (`update_ghl_after_vm_message`): writes Mark as Lead, Support Ticket #2 (identifier), Message body, AI Campaign = Yes, latest lead classification.  
**GHL Path 3** (`_finalize_campaign`): writes Mark as Lead = Yes, AI Campaign = No — ends automated outreach in GHL.

---

## Table 3 — Intent Routing (Answered Calls)

Runs after `run_call_analysis` when call status is `completed`. Intent is classified by GPT-4o mini on the transcript. Priority order is deterministic — higher-priority intents win when multiple signals are present.

| Priority | Intent | Action Taken | Lead Status After | Next Scheduled Job |
|---|---|---|---|---|
| 1 (highest) | `do_not_call` | Set DNC flag; write AI Campaign = No to GHL | `closed` | None — permanent suppression |
| 2 | `wrong_number` | Mark lead invalid; write AI Campaign = No to GHL | `closed` | None |
| 3 | `not_interested` | Close lead; write AI Campaign = No to GHL | `closed` | None |
| 4 | `enrolled` | Confirm enrollment; write AI Campaign = No to GHL | `enrolled` | None — campaign ends on success |
| 5 | `human_transfer_request` | If transfer confirmed: close. If not confirmed: schedule follow-up call +2 h | `human_transfer` | `launch_outbound_call` +2 h (if unconfirmed) |
| 6 | `re_engaged` | Trigger campaign switch: Cold Lead → New Lead | Unchanged (switch handled post-intent) | New Lead campaign entry |
| 7 | `callback_with_time` | Schedule callback at AI-extracted datetime; fallback to +2 h if datetime not extracted | Unchanged | `launch_outbound_call` at extracted time |
| 8 | `callback_request` | Schedule callback +2 h | Unchanged | `launch_outbound_call` +2 h |
| 9 | `failed_booking` | Keep campaign; schedule retry +4 h | Unchanged | `launch_outbound_call` +4 h |
| 10 | `interested_not_now` | Move to nurture; pause outreach for 7 days (configurable) | `nurture` | Nurture scheduler re-enters Cold Lead campaign after delay |
| 11 | `call_later_no_time` | Schedule callback +2 h (same as callback_request) | Unchanged | `launch_outbound_call` +2 h |
| 12 | `uncertain` | Move to nurture with shorter window (½ × nurture_delay_days, min 1 day) | `nurture` | Nurture scheduler re-enters after shorter delay |
| 13 | `partial_engagement` | Schedule retry +2 h. After 2 retries: escalate to Cold Lead campaign | Unchanged (or `low_confidence` on cap) | `launch_outbound_call` +2 h; or Cold Lead entry on escalation |
| 14 | `request_sms` | Schedule an outbound SMS immediately | Unchanged | `send_sms` now |
| 15 | `request_email` | Schedule an outbound email immediately | Unchanged | `send_email` now |
| 16 (lowest) | `low_confidence_audio` | Lifecycle transition to Cold Lead; cancel pending jobs; enter Cold Lead campaign | `low_confidence` → Cold Lead | Cold Lead campaign entry (Synthflow call) |

**Cancellation rule:** When `handle_intent` fires, all other pending jobs for that contact are cancelled before the new action is scheduled. This ensures intent routing always produces exactly one forward action.

---

## Table 4 — GHL Write Paths Summary

| Path | Triggered By | Fields Written | Shadow-Gated? |
|---|---|---|---|
| Path 1 — CRM task | `create_crm_task` after answered call | Mark as Lead, AI Lead Assign To, call summary (Support Ticket #1), AI Lead Classification, AI Campaign; creates GHL task | Yes |
| Path 2 — VM message update | `update_ghl_after_vm_message` after each SMS/email | Mark as Lead = Yes, Support Ticket #2 (identifier), Message body, AI Campaign = Yes, latest lead classification (Support Ticket #4) | Yes |
| Path 3 — Finalization | `_finalize_campaign` at tier 3; or `not_interested` / `enrolled` / `do_not_call` / `wrong_number` intent | Mark as Lead = Yes, AI Campaign = No | Yes |
| Consent writeback | `send_student_summary` | GHL recap field (configured) — student AI summary | Yes — and additionally consent-gated (consent must = YES) |

---

## Table 5 — Job Types Reference

| Job Type | Queue | Module | When Scheduled |
|---|---|---|---|
| `process_webhook` | default | `call_processing.py` | Immediately on webhook receipt |
| `run_call_analysis` | callbacks | `ai_jobs.py` | After `process_webhook` |
| `create_crm_task` | default | `ai_jobs.py` | After `run_call_analysis` |
| `send_student_summary` | default | `ai_jobs.py` | After `run_call_analysis` |
| `update_lead_state` | default | `ai_jobs.py` | After `run_call_analysis` |
| `process_voicemail_tier` | default | `voicemail_jobs.py` | On voicemail/hangup outcome |
| `launch_outbound_call` | callbacks | `outbound_jobs.py` | By tier engine or intent handler; deferred to next calling window if outside hours |
| `synthflow_callback` | callbacks | `voicemail_jobs.py` | By tier engine for tiers 0–2 |
| `send_sms` | default | `channel_jobs.py` | +30 min after every missed call |
| `send_email` | default | `channel_jobs.py` | +30 min after 2nd missed call only |
| `update_ghl_after_vm_message` | default | `channel_jobs.py` | After `send_sms` / `send_email` |
| `collect_metrics` | default | `metrics_jobs.py` | Self-rescheduling every 60 s |

---

## Table 6 — Campaign Entry and Exit Conditions

| Condition | Campaign | Entry / Exit | What Triggers It |
|---|---|---|---|
| New webhook received | New Lead or Cold Lead | Entry | Synthflow webhook with `voice_agent` field identifying campaign |
| `re_engaged` intent on Cold Lead | Cold Lead → New Lead | Switch | AI detects renewed interest on a Cold Lead answered call |
| `low_confidence_audio` intent | Any → Cold Lead | Switch | AI detects inaudible or inconclusive call |
| `partial_engagement` × 2 retries | New Lead → Cold Lead | Escalation | Two +2h retries exhausted without conversion |
| Tier 3 reached (all VMs missed) | Any | Exit — AI Campaign = No | Fourth voicemail unanswered |
| `not_interested` intent | Any | Exit — AI Campaign = No | Lead explicitly declines |
| `enrolled` intent | Any | Exit — AI Campaign = No | Lead confirms enrollment |
| `do_not_call` intent | Any | Exit — DNC suppression | Lead requests no further contact |
| `wrong_number` intent | Any | Exit — marked invalid | Number confirmed wrong |
| Operator force-finalize | Any | Exit — operator action | Dashboard action; all pending jobs cancelled |
