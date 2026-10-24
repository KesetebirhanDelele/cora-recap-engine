# 39 - Failed live transfer: book a callback, and never lose the caller

**Status:** built and tested 2026-10-23 on `fix/failed-transfer-recovery`; deploy pending Kes's go-ahead.

## Problem (call 735331a5, Cold Lead, Fri 2026-10-23 12:46 PM Central)
A caller asked for a human five times. The transfer action `admissions_transfer_roselen` returned
`{"status": "transfer-failed-connection-error", "failed_reason": "transfer_blocked_by_carrier"}`. The voice agent then said "Sorry, I couldn't
transfer you. Would you like to continue with me or try again later?" (a built-in line of the Synthflow transfer action, not our prompt),
repeated the transfer, and ended with "I can't connect you live right this second ... Goodbye". Section 7 of the prompt (offer a callback)
was ignored. Afterwards the engine made it worse:
1. `intent_detection` treated ANY transfer action as a confirmed handoff: it only knew the statuses `failed`, `error`, `timeout`, but
   Synthflow sends `transfer-failed-connection-error` (+ `failed_reason`). The lead was parked as `human_transfer` ("a human took over").
2. The post-call AI analysis chose `create_task = false`, so no staff task existed. Nobody was ever told to call this lead back.

Real transfer outcomes in production (30 days): `transfer-success`; `transfer-failed-connection-error` with `transfer_blocked_by_carrier`
(Roselen's number, 2026-10-22 and 10-23) and `transfer_outside_availability` (support staff).

## Behaviour now
**Voice prompts** (`docs/synthflow-{cold,warm}-lead-prompt.md` are injected on every call; `synthflow-inbound-prompt.md` is pasted by Kes):
new "## 0. HARD RULE" before Section 1: try the transfer once; a failure is any failure / error / silence after "please hold" / any
"couldn't transfer" line; then immediately offer to book a callback with the booking tool, else take name + number (read back) and
offer support@colaberry.com; never say "try again later" or "connecting you now" after a failure; never end the call while the caller
still wants a human unless a callback is booked, details are taken, or the caller says to hang up. Section 7 Step 2 now points to it.

**Engine:**
- `intent_detection._transfer_status_failed` recognises failed transfers (any failure word in `status`, or a non-empty `failed_reason`).
  `_extract_executed_actions` adds `transfer_succeeded`, `transfer_target`, `transfer_reason`. A failed-only transfer returns
  `human_transfer_request` with `transfer_attempted: False`, `transfer_failed: True` (not a confirmed handoff). A failure followed by a
  successful retry counts as connected.
- `app/core/transfer_recovery.py` (pure): `needs_callback(intent, executed_actions)` = a transfer failed, or the intent is
  `human_transfer_request`, AND no transfer connected AND no booking succeeded. `target_kind` reads who was asked for from the action name
  (taiwo / ipbc / payment -> ipbc; support / bala / farhat -> support; else admissions). `build_task` writes "CALL BACK NOW: <name> asked
  for a human and was not connected", due now.
- `crm_jobs.create_crm_task`: when `needs_callback` and the AI analysis did not already create a task, a GHL task is forced, assigned to
  the primary admissions assistant / IPBC assistant / support staff (same `_load_*_ghl_id` helpers as the AI task). An AI-created task is
  left alone. The existing dedupe (one `task_events` row per call) still applies.

## No AI redial after a failed transfer (Kes 2026-10-23)
`_handle_human_transfer_request`: when `entities.transfer_failed` is true NO `launch_outbound_call` is scheduled, and every still-pending
`launch_outbound_call` job of that contact is cancelled (`_cancel_pending_redials`: pending only, that job type only; a call already
running and the contact's other jobs are untouched). The correct recovery is a callback booked INSIDE the call (prompt Section 0) or the
staff task above. An unconfirmed request with no failed-transfer signal keeps the old 2-hour redial (cap `HUMAN_TRANSFER_RETRY_CAP`).

## Alert: `call_transfer_failed` (`app/services/transfer_alert.py`, run each alert cycle)
One warning email (same recipients as the other system alerts) per batch of NEW failed transfers: caller name and phone, campaign,
time, who was asked for (admissions / ipbc / support and the action name), the reason with a plain-English hint
(`transfer_blocked_by_carrier` = carrier / telephony; `transfer_outside_availability` = staff window closed; `transfer_failed` = no
answer), whether a callback was booked on the call, and the call id. A call alerts once (its id is written into the alert, so Resolve
never re-alerts it); the alert resolves itself when no failed transfer is left in the 24 h window. A transfer that failed and then
connected on a retry is not a failure. Read-only on call data.

## Findings from production data (2026-10-23, `call_events.raw_payload_json.executed_actions`)
- Roselen: the current action `admissions_transfer_roselen` last connected on 2026-08-13 20:18 UTC (8 successes 08-07 to 08-13); since then
  19 failures (outside availability 9, no answer 10, to 09-17) and 2 carrier-blocked (10-15, 10-23). The older `admission_roselen`
  action never connected (36 failures, 07-09 to 08-07). A direct-number action (`16825975784`) connected 6 times, last 10-15.
- Taiwo (`taiwo_ipbc_payment`): 15 successes, last 2026-09-28 16:42 UTC; failures 26, all before 08-29; none since.
- Support: `customer_support_farhat` has NEVER connected (13 outside availability, last 10-22 17:16 UTC; 2 no-answer);
  `customer_support_bala` 1 success (08-19) and 1 outside availability (10-15).
- Last booked meeting (`action_ghl_create_booking` success): 2026-10-24 00:41 UTC (Cold Lead); 176 successes in total, 13 Cold Lead and
  4 New Lead in the last 30 days (5 in the week of 10-19, 2 in 10-12, 1 in 10-05, 8 in 09-28); 8 booking errors, the latest on 10-01.

## Acceptance
- Carrier-blocked transfer, no booking -> one task "CALL BACK NOW", assigned to the admissions assistant, due now.
- Connected transfer -> no extra task. Callback booked on the call -> no extra task. AI task already created -> unchanged.
- Both injected outbound prompts begin with the hard rule.

## Tests
`tests/unit/test_transfer_alert_and_redial.py` (11): no redial + pending cancelled (other jobs and a running call untouched), confirmed /
unknown outcomes unchanged, one alert naming the caller, never twice for a call even after Resolve, only the new line on a second alert,
connected transfers silent, callback-booked flag, 24 h window, self-resolve.
`tests/unit/test_transfer_recovery.py` (25): real production status strings, failure-then-success, intent entities, the needs-callback
matrix, target routing, task text, injected prompt content, and `create_crm_task` forcing / not forcing a task.

## Addendum 2026-10-24 - never attempt a transfer when nobody is on shift (Kes)
- `transfer_outside_availability` appeared 47 times in production: the prompts state office hours but give the agent no clock, so it
  attempted the transfer, the Synthflow action refused, and the caller got a failure line.
- `app/core/staff_availability.py` (pure): who is on shift right now, and when the next shift starts. Admissions and Payments / IPBC =
  Mon-Fri 9AM-5PM Central (first name from `admissions_assistants` / `ipbc_assistants`); Support = `support_staff_roster` (overnight
  shifts and Saturday supported). Central time with daylight saving ("CST" in the prompts means Central).
- `availability_block()` builds a "LIVE STATUS" block (current time, per team AVAILABLE NOW / NOT available now + next availability, and the
  rule: not available -> do not transfer, book a callback). `outbound_jobs._availability_note` computes it per call (never raises; on any
  problem the call goes out unchanged) and `SynthflowClient.launch_new_lead_call(availability_note=...)` puts it at the very top of the
  injected prompt.
- Prompt Section 0 step 1 now reads "check availability first, then try the transfer once" (outbound: use LIVE STATUS; inbound: static
  office hours, since Synthflow cannot inject a clock into the inbound agent).
- Alert: an `transfer_outside_availability` failure is not alerted when the agent booked a callback on the call; a carrier block always is.

## Checked 2026-10-24: Cold Lead AND New Lead calls both use the injected prompt
`call_events.model_id` shows ALL outbound calls of the last 30 days (7,348 Cold Lead, 305 New Lead) were handled by one Synthflow assistant,
"Cora - Outbound Admissions Agent - ColdL" (`95fd0659...`), whose saved prompt is just `{prompt}`. The two "New L" assistants in Synthflow
hold old static prompts but are not used. (An earlier note here said New Lead calls probably ignore the injected prompt: that was wrong,
inferred from the stored prompts without checking which assistant handled the calls.)

## Inbound agent update script
`execution/update_synthflow_inbound_prompt.py` (run inside the worker container so the API key is available; never printed):
`--list` (id, name, prompt size; flags the `{prompt}` outbound shape), default dry run (current vs new size + diff summary), `--apply`
(backup file, sends only `agent.prompt`, reads back, checks the prompt and that no other agent setting changed), `--restore FILE`.
Refuses an assistant whose stored prompt is `{prompt}` and the id in SYNTHFLOW_MODEL_ID (not set in production). Inbound agent:
"Cora - Inbound Admissions Agent" (f98454c1-2cd4-476c-b6f2-c5c425689e61). The Synthflow docs do not say how a nested partial update
behaves, so the verification step is part of the script. Tests: `test_update_inbound_prompt_script.py` (13), `test_staff_availability.py` (16).

The inbound doc is kept equal to the LIVE inbound prompt except for the hard-rule block (re-synced 2026-10-24: the live agent says "at no cost";
the doc had drifted back to "free" in 7 places, and a blind `--apply` would have reintroduced it). The doc also drops the one "Free Explorer plan"
left in the live Pricing section. A unit test fails if "free" appears in the inbound prose outside the /start-free link.
