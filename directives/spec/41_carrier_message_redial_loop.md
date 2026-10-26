# 41 - Carrier recordings are not callback requests; call-later redials get a cap

**Status:** built and tested 2026-10-26 on `fix/carrier-message-redial-loop`; deploy pending Kes's go-ahead.

## Problem
The Voice Performance tooltip showed "New Lead 103" for the week of 2026-10-19. Kes read it as 103 new leads. It is 103 CALLS to 40 contacts
(only 31 of them new that week), and 21 of the 103 calls were a bug:
- On a dead or blocked number the "human" in the transcript is a carrier recording: "We could not complete your call. Please try again." or
  "This number is currently not in service. Please try again later."
- `intent_detection` matched "try again" in its `call_later_no_time` patterns, so the engine treated the recording as the lead asking to be
  called later and scheduled a callback ~2 hours out.
- Callbacks of that kind were exempt from the daily call cap (spec/33, `CALLBACK_INTENT_REASONS`) and, unlike human-transfer and failed-booking
  retries, had no retry count. One number was dialled 15 times in 3 days (about 6 a day, 12 seconds each); another 9 times. Over 14 days, 59
  such redial jobs completed across 8 contacts. A call still went out on the evening of 2026-10-23.
- Cost: Synthflow and telephony minutes, and repeated calls to numbers that will never connect.

## Behaviour now
1. **New intent `carrier_message`** (`intent_detection.is_carrier_message`): every `human:` line of the transcript is a carrier / phone-system
   recording (phrases such as "not in service", "could not complete your call", "has been disconnected", "number you have dialed") and the human
   text is short (<= 220 chars; a real conversation is longer). Checked after the executed-actions overrides and BEFORE the call-later patterns.
   The bot's own words never count. A real person saying "call me later" is unchanged.
2. **Handler `_handle_carrier_message`:** no callback is scheduled; pending `launch_outbound_call` redials of the lead are cancelled
   (`_cancel_pending_redials`); the SECOND carrier-recording call inside 30 days marks the number invalid exactly like a wrong number
   (lead_state invalid/closed + AI Campaign off in GHL). The first only logs: one failed connection can be temporary.
3. **`call_later_no_time` has a retry count:** at most `CALL_LATER_RETRY_CAP` = 3 callbacks per lead per `CALL_LATER_WINDOW_DAYS` = 14; past that the
   lead is marked cold and nothing is scheduled (same pattern as human-transfer retries).
4. **Daily cap:** `call_later_no_time` is no longer in `CALLBACK_INTENT_REASONS`, so the 2-calls-per-day cap applies to it, and the slot rebalancer
   may move it. Exact-time and explicit callbacks (`callback_request`, `callback_with_time`) and `transfer_requested` stay exempt.
5. **Dashboard (Voice Performance tooltip):** "Call Type" is relabelled "Calls by type (not leads)" and a new row "New contacts (first call)" shows
   leads whose first call ever fell in that week (per agent when a bar is hovered). Backed by `new_contacts`, `cold_new`, `inbound_new`,
   `new_lead_new` on each weekly point (`dashboard_metrics`; a lead's first call ignores rows with impossible timestamps, 220 exist from before 2020).

## Cleanup of existing data
`execution/fix_carrier_message_calls.py` (dry run by default, counts only): relabels carrier-recording calls from `call_later_no_time` to
`carrier_message`, cancels their pending redials, and marks numbers with 2+ such calls invalid. Run after deploy: dry run, then `--apply`.

## Acceptance
- A transcript whose only human line is a carrier recording gives `carrier_message`; no callback job is created; a waiting one is cancelled.
- Two such calls in 30 days mark the number invalid; one does not; calls older than 30 days do not count.
- A 4th call-later callback in 14 days is not scheduled and the lead goes cold; older callbacks do not use up the cap.
- `call_later_no_time` is subject to the daily cap; explicit callbacks are not.
- Week 2026-10-19 on the tooltip: New contacts (first call) for New Lead = 31 (verified against production: 31).

## Not changed / watch
- Other phone-system recordings (voicemail menus, "person at extension five is on the phone") are not in the carrier list; they are handled by the
  existing voicemail / partial-engagement paths. Add phrases to `CARRIER_PHRASES` if new recordings show up in the transcripts.
- The Synthflow `voice_agent` label can differ from the campaign (the Suresh call was Cold Lead with a NewLead agent label); the chart still groups by
  the agent label.

## Tests
`tests/unit/test_carrier_message_loop.py` (21): carrier detection matrix (including a real conversation and the bot's own words), intent
precedence, first / second / expired strikes, redial cancellation, the call-later cap and window, the daily-cap exemption, and the cleanup script
(dry run changes nothing, apply relabels / cancels / invalidates only repeat offenders, a real callback request stays untouched).
