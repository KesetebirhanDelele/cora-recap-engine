# 35 — Delivery health: sent vs delivered, last activity per channel, silence alert

**Status:** built and tested locally 2026-10-01; deploy pending Kes's go-ahead.

## Problem (Ali, 2026-10-01)
The SMS workflow matched nothing for a month while "attempted" looked healthy and email kept flowing. Controls must be: per channel (not one number), on **delivered** (not attempted), and a **scheduled check for silence** (a dead channel throws no error). Alert Kes, copy Ali, after 2.5 days with no delivery.

## Sources of truth
| Channel | "Sent" (hand-off) | "Delivered" |
|---|---|---|
| SMS | Cora wrote `Support issue Ticket #4` (recorded in `channel_events` kind `handoff`) | GHL message status `delivered` (Twilio status); `undelivered`/`failed` = failed, with the Twilio error (e.g. 30003 unreachable) |
| Email | Cora wrote `Support Issue Ticket #2` + Message | GHL email status behind `meta.email.messageIds[0]` (`GET /conversations/messages/email/{id}`, header 2021-04-15): delivered / opened / clicked = delivered; bounced / failed = failed |
| Calls | completed `launch_outbound_call` job | matching `call_events` row (Synthflow logs reach Cora 10–15 min later): voicemail, human_goodbye, human_pick_up_cut_off, agent_goodbye, or "undefined" with ≥5 s = connected; anything else = failed |

A hand-off matches a delivery for the same contact + channel within [-2 min, +60 min] (calls: +90 min). **Not confirmed** = hand-off older than 15 min (calls 25 min) with no delivery/failure record — the dead-workflow signature. Hand-offs are recorded for follow-ups and corrections (not counted before 2026-10-01 deploy; GHL-side deliveries are backfilled 72 h).

## Replies (inbound)
`delivery_sync` also reads inbound SMS / email / call messages from GHL conversations: logged in `channel_events` kind `reply`, and SMS/email replies are stored in `inbound_messages` (previously never written — so a STOP reply was invisible to Cora's own `_has_stop_reply` check). `lead_state.last_replied_at` is intentionally NOT changed (spec/06: reply handling is owned by GHL automations); only logged. Inbound calls are logged as reply events (they arrive via GHL, not Synthflow).

## Rules (`app/core/channel_health.py`, same for tile colour and alert)
* RED: no delivery for ≥ `channel_silence_hours` (60 = 2.5 days), or ≥20 sent in 24 h with <50 % delivered, or ≥20 sent, >10 % unconfirmed and nothing delivered/failed.
* AMBER: silent ≥ 36 h, delivered rate < 90 %, or >10 % unconfirmed.
* GREY: muted on purpose (`channel_silence_muted`, system paused, calls with outbound campaigns paused) — shown, never alerts.
* A brand-new tracker is not judged until its data covers the silence window (backfill 72 h satisfies this).

## Scheduled check
`alerting.evaluate_alerts` (60 s metrics cycle) calls `delivery_sync.sync` (throttled 5 min, time-boxed 20 s, resumable backfill) and `channel_health.run_silence_check` (hourly). A RED channel raises one `alert_events` row (`channel_silence_<channel>`) and ONE email to `alert_email_to` cc `channel_silence_cc` (default ali@colaberry.com); a recovery email when it clears. Read-only against GHL.

## Dashboard
Tile **Delivery Health** (`/delivery-health`, API `GET /dashboard/delivery-health[/{channel}]`): per-channel sent / delivered (%) / not confirmed / failed / last delivered / 7-day trend, Replies row, drill-down of failed + unconfirmed (contact ids, reasons). Home tile shows the delivered % per channel and a RED count.

## app_config keys
`channel_silence_hours` (60) · `channel_silence_amber_hours` (36) · `channel_silence_cc` · `channel_silence_muted` (comma list: email,sms,call) · `delivery_confirm_minutes` (15) · `delivery_sync_interval_seconds` (300) · `delivery_sync_backfill_hours` (72).

## Limits
* Email "delivered" = provider accepted/delivered, not read. Call "delivered" = connected, not conversation quality.
* Native GHL automations' emails/SMS count toward "last delivered" (the channel works) but not "sent" (no Cora hand-off).
* Needs `ghl_conversations_api_key` (already configured).

## Verification
```sql
SELECT kind, channel, outcome, count(*), max(event_at) FROM channel_events GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT value FROM channel_sync_state WHERE key='delivery_sync';
SELECT alert_type, status, message FROM alert_events WHERE alert_type LIKE 'channel_silence_%' ORDER BY created_at DESC LIMIT 5;
```
Tests: `tests/unit/test_delivery_health.py` (pure + opt-in Postgres: sync, matching, calls, replies, silence alert + cc).

## Leads with no email address (added 2026-10-02)
An email hand-off that GHL never confirms is often not a delivery failure: the GHL contact has no email, so there was nothing to send to.
Measured on Sep 30 - Oct 2: 10 unconfirmed email hand-offs, 7 of them to contacts with no email in GHL (3 had one, one with a typo domain "uahoo.com").
- `mark_no_address` (hourly, after the sync, <= 15 lookups per run) looks each unconfirmed email hand-off's contact up ONCE and stores
  `detail.address_checked` / `detail.no_address` on the hand-off row.
- Outcome `no_address`: excluded from Sent, Not confirmed and Failed, so it can neither lower the delivered rate nor raise the alert; the tile shows
  "+N no email on file (not counted)" under Sent, and the drill-down lists them as "no email on file" so they can be fixed in GHL.
- Not done: stopping Cora from generating an email for a lead with no email address in the first place (the follow-up job does not look the address up).

## Content audit of what GHL sent (added 2026-10-04, Ali 2026-10-03)
The delivery sync already reads every outbound text/email GHL reports (Cora's workflows, the Conversation AI assistant, staff in the app). It now also records `channel_events` rows of
`kind = 'content_flag'` (error = the matched terms, source = GHL's `source`, detail = snippet) for any outbound message that
(1) names a retired course or makes an employment / placement / job-guarantee claim (the `offer_forbidden_terms` list, `app/core/offer.py`), or
(2) answers an opt-out reply (an inbound text our classifier reads as an opt-out) within 3 minutes with anything other than an acknowledgement (`app/core/content_audit.py`).
`run_content_flag_check` (hourly, after the silence check) raises one `ghl_content_flag` alert (email to `alert_email_to`, system-alert headers) while any flag exists in the last 24 h and resolves it when none remain.
**Company name (added 2026-10-12):** `app_config` `brand_phrases` (default "Colaberry School of Data Analytics,School of Data Analytics"; empty disables) is removed from the text before the keyword scan, so a staff email signature does not flag. Only the phrase is removed: a course named beside it is still flagged.
**Banned word "free" and the "excel" fix (added 2026-10-14, Ali 2026-10-04):** `free` is in `DEFAULT_FORBIDDEN_TERMS` (Texas Workforce Commission: a licensed career school may not advertise with it; approved wording is "$0 to start", "No card needed", "Membership from $149/mo"). It is a claim, not a course name, so `content_audit.is_hard_term` keeps it out of the decline judge exactly like an employment claim. Exceptions in `offer._TERM_EXCEPTIONS`: "feel free to ..." and "free time" are not flagged. `excel` no longer matches the verb ("skills needed to excel", "you will excel", "who excel"); "learn Excel", "Microsoft Excel", "Excel course" still hit. The same list drives Cora's own pre-send checks (SMS gate, AI email drafts are rejected and redrafted) and this audit of what GHL sent, so after deploy EVERY GHL text/email containing "free" raises a content-flag alert until the GHL workflows, Conversation AI prompt and knowledge bases are updated. If `app_config.offer_forbidden_terms` is set it REPLACES the default list (it is never merged), so a staged rollout can set it to the default list minus `free` and delete it when GHL is clean.
**Emails delivered to our own staff are not audited (added 2026-10-14):** GHL's AI Receptionist sends an "AI Call Recap" system email to every staff recipient (6 at once) that quotes what the caller said (an alumnus asking about a "senior Power BI developer position"), which raised a `ghl_content_flag` alert per copy. GHL gives no recipient address on the message (`to` / `from` are empty), so `delivery_sync._contact_email` reads the conversation's contact email (cached per process, only when a message would otherwise be flagged). An EMAIL whose contact address is on `app_config.audit_internal_email_domains` (default `colaberry.com,trillionsquare.com`; empty string disables; replaces the list, never merged) is skipped entirely, including employment / placement and "free" hits. Rules: exact domain or a subdomain only (`colaberry.com.evil.com` and `notcolaberry.com` do not match); a missing, unreadable or malformed contact email keeps the message audited (fail closed); texts are always audited; the exemption keys on where GHL delivered the mail, not on a label in the text (a subject line such as "AI Call Recap" was rejected for that reason). Trade-off: a workflow tested by emailing a staff address no longer raises a flag.
**Exemptions (added 2026-10-12; remove course-name hits only - an employment / placement claim is always flagged):**
- *Interview notification (REMOVED 2026-10-13, Ali condition 1):* the `[Interview Notification]` prefix exemption (a password any message could type) is gone, as is the `interview_marker` config key (it was never set in production). Basis: the inbox manager (InboxGenius) SMS / email notices never name the job title (fixed phrase "for the position you applied for"; employer names with a flagged term become "the employer"); the voice call names the position but calls are never scanned; an approver-edited email is also blocked by InboxGenius's send-time audit for any listed title. If a notice must ever name a title, use the signed-proof design in the inbox manager repo's `cora-interview-notice-source-gate-proposal.md` (HMAC proof bound to the exact text), not a prefix. Any SMS / email that names a listed title is now flagged like any other.
- *Correct refusal:* when every hit is a course name, `app/services/content_judge.py` asks a small LLM (`openai_model_content_audit`, JSON output) whether the reply only DECLINES the retired course given the lead's previous message. Only the verdict `declines` removes the flag; `offers`, `unclear`, timeout, API error, bad JSON, `content_llm_enabled=false` or an exhausted per-run budget (shared with the opt-out judge, 8 per sync run) all leave it flagged. Verdicts are cached in memory per message id; a non-declines verdict is stored in the flag's `detail.llm_verdict`.
  - *Hardening (2026-10-13, Ali's review; replay of the 7 stored flags):* (1) the judge is asked `JUDGE_VOTES` = 2 times and `declines` needs every vote to say declines (a split or any `unclear` stays flagged, any `offers` wins); (2) the text the judge reads has unsubscribe / footer sentences removed (`content_audit.strip_footer`; the keyword scan still runs on the full text); (3) a `declines` verdict only counts if the text itself contains a decline phrase ("no longer", "not enrolling / offering / available", "discontinued", "retired"...; `content_judge.has_decline_phrase`), otherwise it is `unclear`. Why: the model alone cleared two mock interview emails (an unsubscribe line "no longer wish to receive these emails" and a job title) in 4-8 of 8 runs. After the change the same messages stay flagged 8 of 8, the real stale Open House email stays flagged 8 of 8, and a genuine refusal still clears 8 of 8. Cost: two LLM calls per judged message.
Limits: texts and emails GHL reports in conversations the sync reads (recent, newest first); Instagram / Facebook / WhatsApp chat replies are only seen if GHL lists them as TYPE_SMS/TYPE_EMAIL.
