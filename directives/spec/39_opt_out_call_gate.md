# 39 - Opt-out call gate: read GHL before every outbound call

**Status:** BUILT 2026-10-03. Context: Ali, 2026-10-03 - the same failure found three times (a flag set in one place that the thing that acts never reads); he asked for this ahead of everything else and for outbound calls to be paused until it exists (paused 18:26 CT).

## Problem
Texts checked GHL tags and do-not-disturb (spec/32, `sms_eligibility`); calls checked only Cora's own `lead_state.do_not_call`. On 2026-10-02 Cora called at least 4 leads GHL tags "do not contact" / "do not call again"; on 2026-10-03, 10 of 59 pending calls were to leads with those tags.

## Rule (acceptance criteria)
Before `launch_outbound_call_job` places a call it reads the lead's live GHL record and **does not call** when any of:
1. `dnd` is true (all channels), or
2. `dndSettings.Call.status` is `active` or `permanent`, or
3. the contact has a tag in `call_block_tags` (app_config, comma separated; default `do not contact, do not call again, do not call, not interested, dnd, unsubscribed`).
Other channels' DND (SMS / Email only) does not block a call.

On a block: this job and every other pending job for the lead are cancelled; the lead is closed in Cora; for "the person asked us to stop" tags and any DND, Cora's own `do_not_call` is also set (so nothing re-enters them); an `audit_log` row `call_blocked_ghl_optout` records the reason. "not interested" closes the lead without setting `do_not_call`.

Fail closed: if GHL cannot be read (outage, rate limit) the call is retried in 5 minutes; after 6 failed attempts the job is cancelled and a warning exception `call_gate_lookup_failed` is raised. A lead with no GHL contact at all is allowed (nothing to read).

Kill switch: `app_config.call_gate_enabled = false` (not recommended). Retry attempts are counted in `audit_log` (`call_gate_lookup_failed`, per job, 2 h window).

## Where
`app/core/call_gate.py` (pure rules), `app/services/call_gate.py` (GHL read, block/defer), hook in `app/worker/jobs/outbound_jobs.py` after the do-not-call and spam-likely guards, before the escalation and student guards. Every call path (campaign entry, voicemail retries, callbacks, nurture) goes through `launch_outbound_call_job`, so one gate covers them all.

## Tests
`tests/unit/test_call_gate.py` (rules incl. negatives, outage fail-closed and give-up, block side effects, not-interested vs do-not-contact), `tests/unit/test_outbound_jobs.py` (block cancels before dialing, defer releases for 300 s, give-up raises the warning exception). `tests/conftest.py` makes the gate allow by default in other tests; opt in with `pytestmark = pytest.mark.real_call_gate`.

## Not covered / follow-ups
- Tags are only a signal; the real fix for tag vs DND is a GHL workflow that sets real DND when "do not contact" is added, plus a reconcile of tagged contacts without DND (proposed).
- The 5 Hz GHL limiter is not applied here (one lookup per call launch, calls are spaced ~75 s).
- Resuming after the 2026-10-03 pause: confirm held jobs are respaced (`rebalance_call_slots`) and inside calling hours.
