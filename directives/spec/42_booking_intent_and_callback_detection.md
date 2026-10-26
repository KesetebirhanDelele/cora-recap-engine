# 42 - A booking is not a callback; callback detection reads the person; callbacks keep their campaign

**Status:** built and tested 2026-10-26 on `fix/booking-intent-and-callback-detection`; deploy follows Kes's go-ahead.

## Problem
25 lead-requested callback jobs created in 30 days (`callback_with_time` 17, `callback_request` 6, `transfer_requested` 2, all launched with no campaign
name) were all cancelled about 2 hours after creation and none ran. Reading the call that created each one:
- **17 were successful bookings.** `intent_detection` returned `callback_with_time` when a booking action succeeded. The handler then scheduled an AI
  redial 2 hours out (no time was extracted) for someone who already had an appointment. The urgent-escalation guard cancelled it at run time because
  `callback_with_time` is an "urgent unresolved" intent (by design: urgent leads are for a human, not for an AI redial). Harmless by luck, wrong by design:
  if the lead had been resolved by a rep the AI would have called a person who just booked.
- **2 were voicemail greetings** read as `callback_request` ("leave a message ... we will call you back").
- **2 matched only the bot's own words.** Callback patterns ran over the whole transcript, bot lines included.
- 133 jobs in 30 days were created by the intent handlers without `campaign_name`; the launcher defaulted to `New_Lead`, so a Cold Lead contact's callback
  went out labelled as a New Lead call (and, because `New_Lead` matches no known campaign, with the New Lead script as a fallback: inferred from code,
  not confirmed on transcripts).

## Behaviour now
1. **New intent `booked`** (`intent_detection`): a successful booking action. `_handle_booked` schedules nothing (handle_intent still cancels the lead's other
   pending jobs first, as for every intent). `booked` keeps exactly the urgency `callback_with_time` had: `escalation_guard._URGENT_INTENTS`, the sales-queue
   urgent-notice set in `alerting`, the dashboard urgent-lead SQL, and score 88 in `dashboard_metrics._INTENT_SCORES`. Staff see no change; the pointless AI redial
   is gone. UI colour added on the AI Performance and Engagement pages.
2. **Callback patterns read only the person's lines.** `callback_with_time`, `callback_request` and `call_later_no_time` are matched against the lines labelled
   `human:` when the transcript has speaker labels; plain unlabelled text is matched whole, as before. Other intents are unchanged.
3. **Answering-machine greetings produce no intent.** If everything the "person" said is short (<= 300 chars) and a voicemail phrase ("leave a message", "after the
   tone", "mailbox", "can't come to the phone", "press 1" ...), `detect_intent` returns None, so no callback, no cold-marking, nothing is scheduled. Carrier
   recordings keep their own intent (spec/41).
4. **Callbacks keep the lead's campaign.** `_schedule_outbound_call` copies `lead_state.campaign_name` into the job payload; the launcher falls back to the lead's
   own campaign (`_lead_campaign_name`) for jobs queued without one, and only then to the legacy `New_Lead`.

## Cleanup of history
`execution/relabel_booked_calls.py` (dry run by default): relabels past `callback_with_time` calls whose executed_actions show a successful booking to `booked`.
Label-only (same score and urgency, no jobs touched).

## Acceptance
- A transcript with a successful booking action returns `booked`, and `handle_intent` leaves no `launch_outbound_call` job for the contact.
- A voicemail greeting returns None; words only the bot said do not create a callback; a real "call me back tomorrow at 3" still does.
- Every callback job built for a lead with a campaign carries it; a lead with no lead_state row still schedules (no campaign key).
- The relabel changes only real bookings: failed bookings, real callback requests and other intents are untouched.

## Not changed / watch
- The Sales Queue and the escalation guard still treat a booked lead as an unresolved urgent lead until a rep logs an outcome. That blocks AI calls for up to 30 days
  after a booking, which is probably what is wanted, but it means booked leads sit in the urgent list. Revisit if the list gets noisy.
- Only the callback family reads the person's lines; the other intents still scan the whole transcript (a possible follow-up).
- `_write_ghl_campaign_off` still writes in shadow mode and cannot update phone-keyed ids (found 2026-10-26, needs Kes's approval to fix).

## Tests
`tests/unit/test_booking_and_callback_detection.py` (23): booked intent and no redial, urgency parity, person-only detection, voicemail greeting matrix,
campaign on callback jobs and the launcher fallback, and the relabel script. `test_live_call_intents.py` updated (booking now returns `booked`).
