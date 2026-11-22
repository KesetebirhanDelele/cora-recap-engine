# Spec 52 - GHL workflows that feed Cora's AI outbound calls

Status: documentation of workflows that live ONLY in GoHighLevel (GHL). Copied on 2026-11-21 from GHL's own plain-language description of each workflow, pasted by Kes. It was NOT checked against the GHL workflow editor, so treat step details as "as described". **When anyone edits these workflows in GHL, update this file.**

The webhook secret used by these workflows is deliberately NOT recorded here. It lives in the Webhook step of each workflow and in the server setting `cora_inbound_webhook_secret`. (The text pasted on 2026-11-21 contained it in plain text for one workflow, so rotating it is advisable: change it in all three workflows and on the server together.)

## Why this matters
These three workflows decide who Cora's AI calls and how often. Cora itself only reacts to the webhook; it does not check the GHL "AI Campaign" field when it dials.

## How the webhook is received (code, verified 2026-11-21)
- `POST /v1/webhooks/leads/{campaign_type}` (`app/api/routes/call_intake.py`), `campaign_type` is `new_lead` or `cold_lead`; any other value returns 404.
- Header `X-Cora-Webhook-Secret` must match `cora_inbound_webhook_secret`; a wrong or missing header returns 401 and raises an `intake_auth_failed` alert (one open row at a time).
- JSON body: Cora reads **`phone`** (must start with `+`, at least 10 characters, else 400 "phone is required and must be E.164"). `first_name` and `email` are accepted and logged only. Any other key, including `phone_number`, is ignored.
- Success returns 202 and calls `enter_campaign()` (`app/core/campaigns.py`): pause switches, urgent-escalation guard, enrolled-student guard, Explorer guard (spec/51), then schedules the first call. Before each dial `launch_outbound_call_job()` repeats the do-not-call, spam-likely, escalation, student and Explorer checks.

## Workflow 1: "Contact Created" -> New Lead
- **Trigger:** Contact Created (any new contact, no extra conditions).
- **If/Else branches (first match wins):**
  1. "Screen Leads w/ no Calls": Phone has a value AND Type = `lead`.
  2. "Screen Leads w/ no Phone Number": Type = `lead` AND Phone has no value AND Email has a value. No actions configured.
  3. "None": everyone else. No actions configured.
- **Actions on branch 1:**
  1. Update Contact Field: AI Campaign (multiselect) = `Yes`; AI Campaign Name (select) = `New_Lead`; AI Campaign Value (select) = `None`.
  2. Number Formatter "Format Phone Number C": `{{contact.phone_raw}}` to E.164, country US; result `{{number_formatter.1.result}}`.
  3. Google Sheets, create row: spreadsheet "Colaberry AI Camapaign DataT" (sic), sheet "NewLeads", columns A to D = formatted phone (with a leading `'` so Sheets keeps it as text), `{{contact.ai_campaign}}`, `{{contact.ai_campaign_name}}`, `{{contact.ai_campaign_value}}`.
  4. Webhook "Cora Outbound - New Leads": POST to `https://204-168-245-238.sslip.io/v1/webhooks/leads/new_lead`; JSON `phone` = formatted phone, `first_name`, `email`; headers `Content-Type: application/json` and `X-Cora-Webhook-Secret`.
- **Settings:** re-entry allowed; stop on response OFF; timezone = account; send window 08:00 to 22:00, every day.
- **Effect:** every new lead with a phone number is put in the AI Campaign (field = Yes) and sent to Cora's New Lead calls at creation. This is how Explorer sign-ups were reached by Cora within hours of signing up (86 of 87 checked). It fires once per contact, at creation.

## Workflow 2: tag "ai cold leads" -> Cold Lead
- **Trigger:** contact tagged `ai cold leads`.
- **Actions:**
  1. Update Contact Field: AI Campaign = `Yes`; AI Campaign Name = `Cold_Lead`; AI Campaign Value = `None`.
  2. Number Formatter "Format Phone Number C" (same as above).
  3. Google Sheets, create row: same spreadsheet, sheet "ColdLeads", columns A to D as in workflow 1.
  4. Wait, business hours only: at least 1 minute, released Monday to Friday 09:00 to 17:00.
  5. Drip mode: batches of **8** every **30 minutes**.
  6. Webhook "Cora Outbound - Cold Leads": POST to `.../v1/webhooks/leads/cold_lead`; JSON `phone` (formatted), `first_name`, `email`; same two headers.
  7. Wait 45 days.
  8. Remove tag `ai cold leads`.
  9. Add tag `ai cold leads ii`.
- **Settings:** re-entry allowed; multiple opportunities allowed; stop on response OFF; auto-mark as read OFF; timezone = account; global window Monday to Friday 09:00 to 17:00.

## Workflow 3: tag "ai cold leads ii" -> Cold Lead
- **Trigger:** contact tagged `ai cold leads ii`.
- **Actions:**
  1. Update Contact Field: AI Campaign = `Yes`; AI Campaign Name = `Cold_Lead`; AI Campaign Value = `None`.
  2. Number Formatter "Format Phone Number" (E.164, US).
  3. Wait, business hours only: at least 1 minute, released Monday to Friday 08:00 to 18:00.
  4. Drip mode: batches of **5** every **30 minutes**.
  5. Webhook "Cora Outbound - Cold Leads": POST to `.../v1/webhooks/leads/cold_lead`; JSON `first_name`, `email`, and the phone under the key **`phone_number`** (workflow 2 uses `phone`); same headers.
  6. Wait 45 days.
  7. Remove tag `ai cold leads ii`.
  8. Add tag `ai cold leads`.
- **Settings:** re-entry allowed; stop on response OFF; timezone = account; global window Monday to Friday 08:00 to 18:00. No Google Sheets step.

## The loop (consequence of workflows 2 and 3)
Adding a tag starts a workflow, and each workflow ends by swapping the tag, which starts the other one. A cold lead is therefore sent to Cora again about every 45 days for as long as the tag swap keeps running, and each send restarts Cora's campaign for that person (`enter_campaign` resets the tier and schedules new calls). If workflow 3's `phone_number` key really is rejected (below), the "ii" rounds place no call and the person is called about every 90 days instead. This is the likely reason for people with 7 or more attempts (10+ dials for 2,410 people). Not proven.

## Open points
1. **`phone_number` vs `phone`:** if workflow 3 truly sends `phone_number`, Cora returns 400 and the "ii" round is silently skipped. Check in GHL (the Webhook step's JSON body) and in the server log for 400s on `/leads/cold_lead`. A quick log check on 2026-11-21 saw only one lead webhook in 72 hours of retained log, so it proved nothing either way.
2. **No guard in GHL against Explorer sign-ups:** workflow 1 marks every new lead AI Campaign = Yes, including Explorer sign-ups, which also keeps them off Rose's call list (the Explorer queue skips AI Campaign = Yes). Wanted: a first branch "tag includes `ai explorer`" with no actions. Cora-side protection exists (spec/51).
3. **No new-lead-only pause:** Cora can pause all outbound campaigns (`outbound_campaigns_paused`, which also holds scheduled calls) or cold leads only (`cold_lead_campaign_paused`).
4. **Whether to keep the 45-day loop** is undecided; see PROGRESS.md 2026-11-21 (late) for the call-volume analysis (attempts 7 and later give 23% of bookings for 62% of calls).
5. Whether the "ai explorer" tag is already on a contact when workflow 1 runs is a question to Ali (sent 2026-11-21, answer pending).
