# Spec 22 — Urgent-Escalation Guard

| Area | Status |
|---|---|
| Pre-call urgent-escalation check (`app/core/escalation_guard.py`) | **DONE.** `check_urgent_unresolved()` — 10 unit tests, all passing. |
| `enter_campaign()` gate | **DONE.** Skips campaign entry, logs a `warning`-severity `outbound_suppressed_urgent_escalation` exception, no jobs scheduled/cancelled. |
| `launch_outbound_call_job()` gate | **DONE.** Belt-and-suspenders — cancels the job + logs the same exception type. Catches jobs scheduled before the escalation occurred, or entered via a path other than `enter_campaign()`. |
| GHL-native (non-Cora) escalation signal ingestion (tags/appointments polled from GHL) | **NOT STARTED — explicitly deferred.** See Out-of-scope. |

## Self-contained problem statement

### Business goal
Stop Cora's outbound campaigns from re-dialing a lead with a cold pitch shortly
after that same lead escalated to a human or had a callback/appointment
booked. Observed incident: a lead asked to speak with a human and had a
callback booked for her via an inbound Synthflow call at 2:54 PM; an unrelated
outbound campaign trigger cold-pitched the same lead 18 minutes later at
3:12 PM, with no awareness anything had happened.

### Relevant systems and files
- `app/core/escalation_guard.py` — new module, `check_urgent_unresolved(session, contact_id)`.
- `app/core/campaigns.py::enter_campaign()` — checked immediately after the
  existing pause-flag checks, before cancelling jobs or scheduling the first call.
- `app/worker/jobs/outbound_jobs.py::launch_outbound_call_job()` — checked
  immediately after the existing blocked-dial-number guard.
- `app/models/call_event.py` (`detected_intent`, `contact_id`, `start_time_utc`,
  `created_at`) and `app/models/lead_state.py` (`sales_outcome`) — both existing
  tables; no schema change was needed.

### Input data
`call_events.detected_intent` (populated by Cora's own intent detection at
call-processing time, see `app/core/intent_detection.py`) and
`lead_state.sales_outcome` (set by a sales rep via the Sales Queue dashboard).

### Expected outputs
`check_urgent_unresolved()` returns a dict (`call_event_id`, `call_id`,
`detected_intent`, `call_time`) when the contact has an unresolved urgent
signal, else `None`. Callers hold/cancel the outbound call and record a
`warning`-severity `outbound_suppressed_urgent_escalation` exception when
non-`None`.

### Known edge cases
- The escalating call and the suppressed outbound call belong to different
  campaigns (e.g. Inbound escalation, Cold Lead outbound trigger) — the guard
  is intentionally campaign-agnostic; any recent urgent intent for the
  `contact_id` blocks any outbound campaign entry.
- A sales rep resolves the lead via the Sales Queue (sets `sales_outcome`)
  *after* the escalation — guard clears immediately, normal cadence resumes.
- Escalation intent is exactly 30 days old — inclusive; still blocks (tested).
- Escalation intent is 31+ days old — does not block; treated as stale.

### Out of scope
- Ingesting escalation/appointment signals that originate entirely outside
  Cora's own calls (e.g. a human rep manually tagging a contact in GHL, or an
  appointment created by a GHL-native flow with no associated `call_events`
  row). This would require polling GHL's Calendars/Appointments and
  Contact-tags API — endpoint and required Private Integration scopes are
  **unverified** (GHL's public docs list a `Version: v3` header for
  `GET /contacts/:contactId/appointments`, inconsistent with the
  `2021-07-28` header this integration uses everywhere else — needs a live
  test against GHL before it's trusted). Deferred as a separate ticket.
- The pre-existing, separately-tracked gap where `launch_outbound_call_job`
  never checks `lead_state.do_not_call` (see `PROGRESS.md`) — not touched by
  this change.

## Acceptance criteria
1. Given a contact with a `call_events` row in the last 30 days whose
   `detected_intent` is `human_transfer_request`, `callback_request`,
   `callback_with_time`, or `enrolled`, and no `lead_state.sales_outcome` set,
   when `enter_campaign()` is called for that contact, then no jobs are
   scheduled and an `outbound_suppressed_urgent_escalation` exception is created.
2. Same signal, but the job was already scheduled before the escalation —
   when `launch_outbound_call_job()` runs, then the job is cancelled (not
   dialed) and the same exception type is created.
3. Given the same signal but `lead_state.sales_outcome` is set, when either
   gate runs, then it passes through with no suppression.
4. Given `detected_intent` is `re_engaged` (or any intent outside the four
   above), when either gate runs, then it passes through — `re_engaged`
   means "call this lead more," not "a human already took over."
5. Given no `call_events` row exists for the contact at all, when either gate
   runs, then it passes through (no false-positive suppression for brand-new leads).

## Constraint architecture
- **Must** check both `enter_campaign()` and `launch_outbound_call_job()` —
  neither alone covers every entry path (nurture scheduler and voicemail-tier
  retries call `launch_outbound_call_job()` without going through `enter_campaign()`).
- **Must** use the narrower `_URGENT_INTENTS` set, not
  `dashboard_metrics._INTENT_SCORES`'s "urgent" (≥80) tier — that tier
  includes `re_engaged`, which has the opposite suppression semantics.
- **Must not** require a schema change — this reads existing `call_events`/`lead_state`
  columns only.
- **Must not** silently drop a suppressed lead — every suppression creates an
  auditable `exceptions` row (`entity_type="lead"`, `entity_id=contact_id`).
- **Escalation trigger:** if a future change wants this guard to also consult
  a GHL-native (non-Cora-call) signal, treat that as a new, separately-scoped
  ticket — do not bolt unverified GHL API calls onto this guard without first
  confirming the endpoint/scope live.

## Eval design
`tests/unit/test_escalation_guard.py` (10 cases: no signal, no contact_id,
each of the four urgent intents, a non-urgent intent, 31-days-stale,
29-days-fresh boundary, resolved-by-rep, most-recent-call-wins) +
`tests/unit/test_campaigns.py` (skip-on-unresolved, proceed-on-resolved) +
`tests/unit/test_outbound_jobs.py` (cancel-on-unresolved, proceed-on-none).
