# 37 — One offer (AI Systems Architect Accelerator) and SMS = notification, email = program description

**Status:** built and tested locally 2026-10-01; deploy pending Kes's go-ahead.

## Problem
* The follow-up generator hard-coded "learning Data Analytics or AI" and supplied no product facts, so the model described a retired offer: of 3,528 SMS in 14 days 1,629 (46 %) said Data Analytics; 145 of 229 emails (63 %) did, 39 named Power BI / Tableau / SQL. The success-story library (228 transcripts, 5 injected per message) is all alumni of the retired program (92 explicit, 28 more about data/analytics).
* SMS carried marketing (class start, Open House, links, stories). Kes (2026-10-01): **SMS is a notification for missed calls (and a reminder of an upcoming call), at one segment if possible, two at most; email is where the program is described.** The only course on offer is the AI Systems Architect Accelerator.
* One LLM call produced both channels and `send_sms_job` / `send_email_job` each threw half away; 28 % of SMS used a curly apostrophe, switching the text to UCS-2 (a 180-character text billed 3 segments).

## Behaviour
| | SMS (`vm_sms_notice`) | Email (`vm_followup_generator`) |
|---|---|---|
| Purpose | "we tried to reach you - when is a good time?" | describes the one program, Open House / explainer / free-start next steps |
| Offer | names the Accelerator at most once as the reason for the call; never describes it | the Accelerator only, using `offer_facts` (12 weeks, online, ~4 h/week live + recorded, Claude Code / Claude API / MCP / Docker / GitHub, capstone Expo, CCA-F prep, free start at myfreeaiclass.com) |
| Never | links, dates, prices, enrollment push, stories, urgency, other courses, "you called us" | other courses, stories/testimonials, salary or placement claims, invented dates/prices, a "Text STOP" line (the platform adds the footer) |
| Length | <= 160 chars incl. opt-out on the first draft (1 segment), hard ceiling 240 (2 segments) | 130-190 words |
Generation: `generate_vm_followup(..., channel="sms"|"email")` - only the needed channel is generated (half the LLM calls). Tier by attempt: tier_1..tier_3, tier_final.

## Enforcement (deterministic - prompts alone are probabilistic)
1. Generator: draft checked against the forbidden-term list (SMS also marketing wording and length); one corrective retry, then a safe fallback (SMS: "Hi {name}, it's Cora from Colaberry. I just tried to call you about the AI Systems Architect Accelerator. What time works for you to talk?"; email: generic note). SMS is normalised to plain GSM text (`normalize_sms`) and always ends with the opt-out line.
2. Pre-send gate (spec/34): retired-course terms are blocked in **every** SMS; marketing wording is blocked in follow-up SMS (not in date corrections / test sends); `sms_max_segments_per_message` default 4 -> **2**.
3. Routing normalises the text again before it is written to GHL.

## app_config
`offer_name` (AI Systems Architect Accelerator) · `offer_facts` (see above, `{free_url}` placeholder) · `offer_forbidden_terms` (data analytics, data analyst, data science, bootcamp, power bi, tableau, sql, excel, full stack, cybersecurity, business intelligence) · `sms_max_segments_per_message` (2).

## Not done / open
* **Upcoming-call reminder SMS: not built, by decision (Kes 2026-10-01) - no reminder logic existed and none is to be added.**
* Student stories are off until Accelerator stories exist (`video_transcripts.csv` is entirely the retired program).
* Enrollment model (Kes 2026-10-01): enrollment is ongoing for the SELF-PACED option (start any time, free start at myfreeaiclass.com); the Open House is for people who want LIVE classes. `offer_facts` and the email prompt say so; the email follows the Settings schedule block for Open House / class-start dates.
* Texts Synthflow or GHL-native automations send do not pass this generator or gate - out of scope by decision (Kes 2026-10-01).
* Dead code: `vm_content_generator` / `services/ai.generate_voicemail_content` have no callers.

## Tests
`tests/unit/test_sms_notice.py` (31): forbidden terms (whole words), marketing detection, gate rules, 2-segment default, GSM normalisation, every prompt tier free of retired names / stories, SMS draft -> retry -> fallback, length trim, per-channel generation.

## Addendum 2026-10-04
- **Employment claims:** `employment rate, placement rate, job placement, job guarantee, guaranteed job, hiring rate, employment outcome` are in the default forbidden terms (Ali: never, from anything, on any channel, unless he approves the number and its source).
- **One segment, always:** the missed-call text is retried up to three times to fit 160 characters; the deterministic fallback is ~150 characters; a two-segment draft is no longer accepted.
- **Email follow-ups obey GHL opt-outs:** `build_followup_updates` now applies to the email route the same eligibility the text route uses (GHL do-not-disturb on all channels or Email, opt-out tags, no email address, unreadable record = fail closed); a skip removes the send-trigger fields (Ticket #2 + Message).
- **Skips leave a trace:** `enter_campaign` writes `audit_log` rows `campaign_entry_skipped` (reason: outbound_campaigns_paused, cold_lead_campaign_paused, do_not_call, urgent_escalation_unresolved, enrolled_student, no_phone_number).
