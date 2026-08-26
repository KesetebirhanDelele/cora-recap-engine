# Spec 25 — GHL Conversation-Context Phone Lookup Fix

| Area | Status |
|---|---|
| `_fetch_ghl_messages()` phone selection (`app/core/conversation_context.py`) | **DONE.** Drops the direction branch; always prefers `phone_number_to`, falls back to `phone_number_from`. |

## Self-contained problem statement

### Business goal
Stop `_fetch_ghl_messages()` from looking up the wrong GHL contact (or none at
all) when assembling conversation history for AI message generation on
inbound calls.

### Relevant systems and files
`app/core/conversation_context.py::_fetch_ghl_messages()`.

### Root cause
Same root cause as [spec/24](24_normalized_phone_agent_line_fix.md), a second
call site. This function was direction-aware — `inbound` →
`phone_number_from`, else → `phone_number_to` — on the intuitive but false
assumption that `phone_number_from` is the caller's number on an inbound
call. Spec/24's prod-data check (phone_number_to matches contact_id ~80% of
the time vs. <2% for phone_number_from, on both inbound and outbound calls)
disproves that for this Synthflow integration: `phone_number_from` is
consistently Synthflow's own agent line, `phone_number_to` is consistently
the external/lead number, regardless of direction.

Practical effect of the bug: for any inbound call, `GHLClient.search_contact_by_phone()`
was called with the agent's own line, not the lead's — so the GHL-contact
lookup either found nothing (returning `[]`, silently degrading to
no-history generation) or, worse, matched some other unrelated GHL contact
who happens to share the agent's line as a stored number.

### Expected outputs
`_fetch_ghl_messages()` resolves phone from `phone_number_to` first,
`phone_number_from` only as a fallback when `phone_number_to` is absent —
identical priority to the fix already shipped in `ai_jobs.py` /
`lifecycle_jobs.py` (spec/24).

### Out of scope
No other call sites reference `phone_number_from`/`phone_number_to`
ambiguously — spec/24 covered `ai_jobs.py` and `lifecycle_jobs.py`; this
covers the one remaining site found during that investigation.

## Acceptance criteria
1. Given the most recent `call_event` for a contact has both
   `phone_number_from` (agent line) and `phone_number_to` (lead's number),
   when `_fetch_ghl_messages()` runs, then the GHL phone lookup uses
   `phone_number_to` — regardless of `call_event.direction`.
2. Given only `phone_number_from` is present, when `_fetch_ghl_messages()`
   runs, then it falls back to `phone_number_from` rather than returning `[]`.
3. Given neither field is present, behavior is unchanged — returns `[]`.

## Constraint architecture
- **Must not** change the non-fatal failure mode — any GHL error or missing
  phone still returns `[]`, never raises.
- **Must** keep the existing message-normalization, dedupe, and limit logic
  untouched — only the phone-resolution step changes.

## Eval design
`tests/unit/test_conversation_context_ghl.py`:
- `test_fetch_inbound_prefers_phone_to_over_agent_line` (replaces
  `test_fetch_inbound_uses_phone_from`, which asserted the disproven
  behavior) — inbound call, both fields present, asserts `phone_number_to`
  is used.
- `test_fetch_outbound_uses_phone_to` — unchanged, still passes.
- `test_fetch_falls_back_to_phone_from_when_phone_to_absent` — new,
  covers acceptance criterion 2.
