# Spec 24 — `normalized_phone` Agent-Line Corruption Fix

| Area | Status |
|---|---|
| Fallback-chain fix (`ai_jobs.py`, `lifecycle_jobs.py`) | **DONE.** Prioritizes `phone_number_to` over `phone_number_from`, direction-independent (see Root cause — reversed from the initial draft's direction-aware assumption after checking prod data). 4 new/regression unit tests, all passing. |
| Dial-target fix (`campaigns.py::enter_campaign()`) | **DONE.** Prefers `contact_id` over `normalized_phone` when `contact_id` is phone-shaped. 2 new unit tests, all passing. |
| Full local unit suite | **DONE.** 1159 passed, same 7 pre-existing failures as baseline (env-only gaps, confirmed via `git stash` diff), `ruff` clean vs. baseline. Not yet committed. |
| Prod backfill (35 corrupted `lead_state` rows) | **NOT STARTED — requires Kes's explicit approval before running against prod.** |

## Self-contained problem statement

### Business goal
Stop Cora from silently attempting to dial the Synthflow agent's own inbound
line (`+16822812224`) instead of a lead's real number when scheduling
outbound calls. Left unfixed, affected leads never actually get called on
re-engagement/cold-lead triggers, with no alert — the one case that did alert
(`blocked_dial_number`, job `372fe32c…`, contact `+13104186986`,
2026-08-26 16:05 UTC) only fired because the corrupted number happened to
already be on the hardcoded `BLOCKED_DIAL_NUMBERS` blocklist.

### Relevant systems and files
- `app/worker/jobs/ai_jobs.py::run_call_analysis()` — `derived_phone` fallback
  chain (~line 221) used when creating a `LeadState` stub for a brand-new
  inbound caller.
- `app/worker/jobs/lifecycle_jobs.py::update_lead_state()` — identical
  `derived_phone` fallback chain (~line 148), same bug, same fix.
- `app/worker/jobs/voicemail_jobs.py::process_voicemail_tier()` — narrower
  fallback (`phone_number` → `phone` → `contact_id`), does **not** include
  `phone_number_to`. Not buggy; left unchanged. Confirmed by direct read.
- `app/core/campaigns.py::enter_campaign()` (~line 205) — builds the outbound
  `launch_outbound_call` job payload from `lead.normalized_phone` directly,
  with no fallback to `contact_id` even when `contact_id` is itself a
  verified phone number (the phone-derived-contact_id convention documented
  in `call_intake.py`).
- `lead_state` table, prod — 35 rows currently have
  `normalized_phone = '+16822812224'` (queried via SSH into the Hetzner box,
  2026-08-26; see chat history for the full row dump). All 35 have a
  phone-shaped `contact_id` that differs from `normalized_phone`.

### Input data
`call_events.raw_payload_json` fields `phone_number_from` / `phone_number_to`
/ `phone_number` / `phone`, as sent by Synthflow on inbound-call webhooks.

### Root cause
Both buggy call sites derive `normalized_phone` with:
```
phone_number_from → phone_number_to → phone_number → phone → contact_id
```
This assumes `phone_number_from` is the lead's number. **Verified false
against prod data.** Queried all `call_events` with a `phone_number_to`
field, grouped by `direction`, checking which field matches the (phone-shaped)
`contact_id`:

| direction  | rows   | `phone_number_to` matches contact_id | `phone_number_from` matches |
|---|---|---|---|
| `inbound`  | 1,134  | 904 (80%)                             | 16 (1.4%)                   |
| `outbound` | 26,902 | 20,964 (78%)                          | 47 (0.17%)                  |

In Synthflow's payload schema, `phone_number_to` is consistently the
external/lead number **regardless of call direction**; `phone_number_from`
is almost always one of Synthflow's own agent-line numbers (e.g.
`+16822812224` for inbound, `+19729921028` for the New Lead outbound
campaign — both already present in `BLOCKED_DIAL_NUMBERS`). Because
`phone_number_from` is *always present* on inbound payloads (never null), the
old fallback chain never even reached `phone_number_to` — it took the wrong
field directly, every time, for every inbound-originated stub. That
corrupted value then gets written to `lead_state.normalized_phone` and is
later trusted verbatim by `campaigns.py::enter_campaign()` as the number to
dial.

An initial draft of this fix assumed the opposite — that the right call was
direction-aware (`inbound` → `phone_number_from`, `outbound` →
`phone_number_to`), mirroring the pattern already in
`app/core/conversation_context.py::_fetch_ghl_messages()`. The prod-data
check above disproves that for this call site: direction-awareness would
have reproduced the exact same bug for inbound calls. **The fix implemented
here is direction-independent: always prefer `phone_number_to`.**

**Related, unconfirmed suspicion (out of scope for this fix):**
`conversation_context.py`'s inbound branch does exactly what the prod data
says is wrong (uses `phone_number_from` for inbound), which would feed the
agent's own number into the GHL-contact-by-phone lookup for every inbound
call's conversation-context fetch. Not touched here — flagged for a separate
decision, since it's a different code path with its own tests
(`test_conversation_context_ghl.py`) asserting the current (likely-wrong)
behavior as intentional, and deserves its own verification pass rather than
a same-diff opportunistic fix.

### Expected outputs
- New `LeadState` stubs never derive `normalized_phone` from
  `phone_number_to`.
- `enter_campaign()` dials `contact_id` (not `normalized_phone`) whenever
  `contact_id` is phone-shaped, since that's the only field in this data
  model with a documented invariant of being a real, dialable number.
- No behavior change for GHL-ID-style `contact_id`s (non-phone-shaped) —
  those still require `normalized_phone` to be present, unchanged from today.

### Known edge cases
- `contact_id` is phone-shaped (`+1...`) and `normalized_phone` is also
  correctly populated and matches — no change in behavior, just a different
  code path to the same value.
- `contact_id` is phone-shaped and `normalized_phone` is missing entirely —
  today this already logs a warning and skips scheduling
  (`campaigns.py:206-211`); with the fix, this case now succeeds using
  `contact_id` instead of skipping. This is a strict improvement, not a
  behavior regression — covered by a new eval case.
- `contact_id` is a GHL-native ID (not phone-shaped) and `normalized_phone`
  is missing — unchanged, still skips with a warning (no dialable number
  exists anywhere in this row).
- Historical corrupted rows (the 35) are **not** self-healing — the code fix
  only prevents new corruption. See backfill section.

### Out of scope
- The 35-row prod backfill is a separate, explicitly-gated step (see below)
  — not bundled into this code change's approval.
- `voicemail_jobs.py` — confirmed clean, not touched.
- Any change to `BLOCKED_DIAL_NUMBERS` contents — that list continues to
  serve as the belt-and-suspenders backstop regardless of this fix.

## Acceptance criteria
1. Given an inbound call event whose `raw_payload_json` has both
   `phone_number_from` (agent line) and `phone_number_to` (lead's number),
   when a new `LeadState` stub is created (either `ai_jobs.py` or
   `lifecycle_jobs.py` path), then `normalized_phone` is set to
   `phone_number_to`, not `phone_number_from` — regardless of
   `call_event.direction`.
2. Given `lead.contact_id` is phone-shaped and `lead.normalized_phone` is
   `None` or corrupted (e.g. equals a known agent line), when
   `enter_campaign()` schedules the first outbound call, then the job
   payload's `phone_number` equals `contact_id`.
3. Given `lead.contact_id` is not phone-shaped (GHL ID) and
   `lead.normalized_phone` is `None`, when `enter_campaign()` runs, then it
   still skips scheduling and logs the existing warning — unchanged from
   current behavior.
4. Given `lead.normalized_phone` is present and valid, when
   `enter_campaign()` runs, then behavior is unchanged (uses
   `normalized_phone`, matching `contact_id` in the phone-derived case
   anyway).

## Constraint architecture
- **Must not** change `voicemail_jobs.py` — its fallback chain doesn't
  include `phone_number_to` and isn't implicated.
- **Must not** change `BLOCKED_DIAL_NUMBERS` semantics or the
  `blocked_dial_number` guard itself — it stays as the backstop.
- **Must not** run the prod backfill as part of this code change without a
  separate, explicit go-ahead from Kes — this is a direct prod data write,
  gated under this repo's approval rule for production-impacting changes.
- **Must** add regression tests covering both fixed call sites and the new
  `enter_campaign()` fallback.
- **Preference**: keep the `contact_id`-is-phone-shaped check consistent
  with the existing convention elsewhere in the codebase (`.startswith("+")`,
  as already used in `ai_jobs.py`, `lifecycle_jobs.py`, `call_intake.py`).

## Decomposition
1. Fix `ai_jobs.py` + `lifecycle_jobs.py` fallback chains — independently
   testable, no dependency on the campaigns.py fix.
2. Fix `campaigns.py::enter_campaign()` dial-target selection — independently
   testable.
3. Add/update unit tests for both.
4. Run full local unit suite, confirm no regressions.
5. Debrief Kes; on approval, backfill prod (separate, explicit step).

## Eval design
- `tests/unit/test_lifecycle_jobs.py` / new or existing `ai_jobs` test file:
  add a case with `phone_number_from` absent, `phone_number_to` present —
  assert `normalized_phone` is NOT `phone_number_to`.
- `tests/unit/test_campaigns.py`: add a case with phone-shaped `contact_id`,
  `normalized_phone=None` — assert job is scheduled with
  `phone_number == contact_id` (previously: skipped entirely).
- `tests/unit/test_campaigns.py`: add a case with non-phone `contact_id`,
  `normalized_phone=None` — assert still skips (regression guard for
  acceptance criterion 3).
- Full `python -m pytest tests/unit/ -q` run, diffed against pre-change
  failure baseline per this repo's existing pattern (PROGRESS.md 2026-07-17
  entry).
