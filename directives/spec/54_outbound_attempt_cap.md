# Spec 54 - Per-person cap on Cora's AI outbound attempts

Status: merged and deployed 2026-11-22 (87bfbe3). Decision: Kes, 2026-11-22.

## Problem
Measured 2026-11-21 for the new AI class (calls from 23 Aug 2026, bookings recorded): attempts 7 and later are 62% of calls and 63% of minutes but 23% of bookings. Booking rate falls from 1.38% (1st attempt) to 0.13% (7th or later); minutes per booking rise from 42 to 464. Ali independently reached the same totals and recommends a cap of 6 (keeps 59 of 77 bookings for 37% of the minutes). Attempt counts there are each person's lifetime attempts since July 2025.

## Rule (`app/core/attempt_cap.py`)
Block a call when ALL of these hold:
1. the person's lifetime AI outbound attempts >= `outbound_attempt_cap` (6); and
2. the person has had >= `outbound_attempt_cap_era_guarantee` (3) attempts since `outbound_attempt_cap_era_start` (2026-08-23, when the AI class first appears in call prompts; not verified to be the exact switch date); and
3. the number is not in the uncapped holdout (`outbound_attempt_cap_holdout_percent`, 10%, chosen by a hash of the last 10 digits so a person is always in or out).

Condition 2 is the "AI-class guarantee": someone dialed many times under the old Data Analytics pitch still hears the new pitch up to 3 times. Condition 3 is the comparison group: read it after 4 to 6 weeks (bookings first; Explorer account creation from Ali's weekly file where it applies).

An attempt = one outbound ColdLead/NewLead row in `call_events` (answered or not), `report_excluded` false, keyed on `contact_id` = the phone number (+1XXXXXXXXXX, 1XXXXXXXXXX or XXXXXXXXXX). Older rows without a `contact_id` are not counted, so the cap errs toward allowing a call.

## Where it applies (every Synthflow outbound call passes `launch_outbound_call_job`, the only caller of `launch_new_lead_call`)
- `enter_campaign()`: skips entry, skip reason `attempt_cap_reached` (this is what stops the 45-day cold-lead re-sends, see spec/52).
- `launch_outbound_call_job()`: checked again at dial time (database only, before the guards that call GHL); cancels the job; no dashboard exception (expected suppression).

## Switches
`outbound_attempt_cap_enabled` (true), `outbound_attempt_cap` (6), `outbound_attempt_cap_era_start` ("2026-08-23"), `outbound_attempt_cap_era_guarantee` (3), `outbound_attempt_cap_holdout_percent` (10; 0 turns the holdout off). Fails OPEN on any lookup error or unusable setting.

## Expected effect on the data at 2026-11-22 (read-only preview)
- 2,879 people have lifetime >= 6 and >= 3 attempts since 23 Aug: blocked (about 10% of them fall in the holdout and are not).
- 1,833 people have lifetime >= 6 but fewer than 3 since 23 Aug: allowed by the guarantee.
- Of 185 scheduled calls, 110 belong to blocked people and would be cancelled at dial time (about 10% fewer after the holdout).

## Must / Must-not
- Must: leave Inbound, staff calls and callbacks alone; count only AI outbound.
- Must not: block anyone below the cap, or anyone in the holdout.
- Escalation: if bookings per week fall by clearly more than the calls do, turn it off with `outbound_attempt_cap_enabled=false` and tell Ali.

## Tests
`tests/unit/test_attempt_cap.py` (decision table incl. exactly-at-cap and guarantee cases, holdout determinism and share, phone spellings, counting excludes inbound/other agents/excluded/other numbers, fail-open, defaults); `test_campaigns.py` (entry skipped, settings None); `test_outbound_jobs.py` (dial cancelled, nothing placed, no exception).

## Known limits
- Only 77 bookings behind the decision; the holdout is how it gets confirmed.
- Enrollment cannot be measured (no enrollment dates); judge on bookings, and on account creation for Explorer people.
- The Synthflow price per minute is not in our records, so no dollar figure.

- Exempt: a call the lead asked for (`callback_request`, `callback_with_time`, `transfer_requested`, same set as the daily-cap exemption in `app/core/call_policy.py`). The cap is not consulted for those.

## Monitoring (commitments to Ali, 2026-11-23)
Ali (2026-11-22) showed that a 10% holdout read at six weeks collects too few bookings to separate "no cost" from "halved". Kes agreed (draft reply, 2026-11-23). Corrections and commitments:
- The holdout only keeps calls the cap would have stopped, so it costs roughly a tenth of THOSE calls (about 15 of 110 on 2026-11-22), not a tenth of all calling. For the same reason it collects about one booking in six weeks (attempts 7+ gave about 18 of 77 bookings in 91 days; a tenth is under 0.02 a day). The same figure bounds the worst-case cost of the cap at about 18 bookings in 91 days, before the AI-class guarantee. From the 2026-11-21 analysis; not re-run on live data since go-live.
- **Keep the 10% holdout.** Read bookings at 6 and 12 months (about 2027-05-22 and 2027-11-22), not at six weeks.
- **Weekly read (built):** `execution/attempt_cap_weekly_report.py` (read-only, counts only). Compares the people who were at the cap on go-live, capped vs holdout, per 1,000 people, on calls placed, calls reached (status `completed`) and bookings (`detected_intent = booked`), plus all-calls totals against the 4 weeks before go-live. Go-live is taken as 2026-11-22 17:10 UTC (worker container rebuilt 17:06 UTC). First report due after the first full week (about 2026-11-30). Run: `docker compose exec -T worker-quality python -m execution.attempt_cap_weekly_report` (after a deploy), or pipe the file to `python -` in the container.
- **Switch-off check (built into the report):** needs 2 complete weeks after go-live; flags REVIEW when the weekly bookings fall 30 or more percentage points more than weekly calls versus the 4-week baseline. The 30 points is PROPOSED, not yet confirmed by Kes; bookings are few per week (about 11), so a flag means look, not automatic switch-off. If confirmed: `outbound_attempt_cap_enabled=false` and tell Ali.
- Capped-group calls after go-live should be near zero (callbacks the lead asked for are exempt); anything larger is a leak to investigate.
- "Reached" is a rule on the call record (status completed: a person picked up), not a judge of the transcript; voicemail, no-answer, busy and failed are excluded.
- **Connected calls and the clock (Ali, 2026-11-23):** the report counts `connected` calls (status completed or hangup_on_voicemail: the call got past ringing; failed, no-answer and busy did not) for both groups. Week 1 is the first week from go-live with any connected call. Until then it prints NO CONNECTED CALLS plus the attempt count, compares nothing, and the switch-off check says WAITING. Ali's concern was a different Synthflow voice agent whose calls fail; Cora's own agents were connecting (2026-11-23 live run: 44 of 48 calls since go-live connected, 12 reached), so for Cora the clock starts in week 1 as planned. 'Reached' (a person picked up) stays the measure.
- Tests: `tests/unit/test_attempt_cap_weekly_report.py` (12).
