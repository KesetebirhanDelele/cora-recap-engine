# Spec 23 — Staff Call Quality Analysis

| Area | Status |
|---|---|
| GHL client: `search_conversations`, `get_message_recording`, `get_message_transcription`, `api_key_override` (`app/adapters/ghl.py`) | **DONE.** All three validated live against real GHL data before being written permanently. 15 unit tests. |
| Transcription fallback: `OpenAIClient.transcribe_audio()` (`app/adapters/openai_client.py`) | **DONE.** Validated live (real GHL recording → Whisper, ~6s round trip for a 49s call). 8 unit tests. |
| Schema: `staff_call_quality` table (migration `0021`, `app/models/staff_call_quality.py`) | **DONE.** Not yet applied to production — ships with this spec's PR. |
| Routing: `app/core/call_classification.py` | **DONE.** GHL tags → GHL fields → enrollment_date → call history → AI transcript fallback. 45 unit tests (includes catching a real `.format()` brace-escaping bug in the AI prompt before it ever ran). |
| `lead_state.do_not_call` gate (`app/core/campaigns.py`, `app/worker/jobs/outbound_jobs.py`) | **DONE.** Closes a previously-known, separately-tracked gap (`PROGRESS.md`, 2026-07-15 session) — unrelated to spec/23's core scope but requested alongside it. Same belt-and-suspenders pattern as spec/22's escalation guard. |
| Support-ticket context: `app/core/ghl_support_context.py` | **DONE.** 8 unit tests. |
| Rubrics + scoring: `app/core/call_quality_scoring.py` | **DONE.** Sales rubric compliance dimension sourced from `docs/synthflow-{warm,cold}-lead-prompt.md` §5, not invented. Support rubric judges each call against the student's actual request (identified from the transcript), not a fixed checklist. 15 unit tests. |
| Discovery/scan job: `app/worker/jobs/staff_call_quality_jobs.py` | **DONE**, gated **off by default** (`STAFF_CALL_QUALITY_SCAN_ENABLED=false`) — see Constraints. 19 unit tests. |
| Dashboard page | **NOT STARTED** — deferred, see Out-of-scope. |
| GHL-native escalation ingestion (unrelated prior work, spec/22) | Out of scope here — see spec/22. |

## Self-contained problem statement

### Business goal
Kes needs visibility into the quality of calls between human sales reps /
support staff and leads/students — something Cora has no visibility into
today, since those calls are placed through GHL's own native dialer
(LC Phone), entirely outside Cora's Synthflow-based call pipeline.

### Relevant systems and files
- GHL Conversations API (recording + transcription endpoints) — confirmed
  live this session; not previously used anywhere in this codebase.
- `app/adapters/ghl.py::GHLClient` — extended with `api_key_override` (a
  second Private Integration token, `GHL_CONVERSATIONS_API_KEY`, is
  required — `GHL_API_KEY` has no Conversations scope by design) and three
  new read methods.
- `app/adapters/openai_client.py::OpenAIClient.transcribe_audio()` — GHL is
  not generating its own transcripts on this account (confirmed live,
  `CONVERSATIONS_MSG_RECORDING_NOT_FOUND` for a 4-month-old completed call —
  not a processing-delay issue), so this is the primary transcript source,
  not a fallback in practice.
- `app/core/call_classification.py`, `app/core/ghl_support_context.py`,
  `app/core/call_quality_scoring.py` — routing and scoring logic.
- `app/worker/jobs/staff_call_quality_jobs.py` — the periodic scan, same
  claim/run/reschedule shape as `webhook_recovery_jobs.py`.
- `docs/synthflow-warm-lead-prompt.md`, `docs/synthflow-cold-lead-prompt.md`
  §5 — source of the sales rubric's compliance-disclosure checklist.

### Input data
GHL Conversations (search, messages, recording, transcription), GHL contact
custom fields (routing signals + support-ticket context), and Cora's own
`call_events`/`lead_state` (the call-history routing fallback).

### Expected outputs
One `staff_call_quality` row per GHL call message: connection status,
transcript + source, `conversation_type` + how it was determined, rubric
score + breakdown + summary, `flagged_reason` when something needs human
attention.

### Known edge cases
- **GHL tags are the primary routing signal, not the picklist fields**:
  per Kes (2026-08-25), reviewing real contact records — any tag
  containing "student"/"enrolled" means the contact is a student; tags
  present but none matching means lead (Colaberry tags leads with
  campaign labels like "warm lead"/"ai cold leads ii" but only explicitly
  tags the enrolled-student exception). Checked first in the priority
  chain, since real contacts had tags populated when the picklist fields
  below were empty. An empty tag list is treated differently from "has
  tags but none say student" — it falls through rather than defaulting to
  sales on zero information.
- **Duplicate GHL fields**: three near-identical picklist fields (`Who you
  are`, `Select an option that best describes you` ×2) share the exact
  options (`Potential Student`/`Current Student`/`Business or Partner`) —
  likely leftover form duplicates. Which is actually populated in practice
  is unknown as of 2026-08-25 (checked as the fallback when tags don't
  resolve it). Per Kes: keep checking all three, persist the raw value of
  each on every row, decide which is authoritative once real data
  exists — do not consolidate yet.
- **Stale support-ticket fields**: Colaberry tracks up to 4 sequential
  support tickets via numbered duplicate fields with inconsistent
  capitalization (`Support Issue Ticket #1` vs `Support issue Ticket #3` —
  real GHL data, not a typo). No per-field timestamp exists, so these
  can't be reliably matched to *this specific* call — used as supporting
  context only; the transcript is the primary source for "what was
  requested."
- **`lastMessageType` search limitation**: `GET /conversations/search`
  filters on a conversation's *most recent* message — a call followed by a
  text before the next scan drops out of the filter. Acceptable for a
  15-minute-interval discovery job; not reliable for a one-shot historical
  backfill (not attempted here).
- **Non-connected calls** (busy/no-answer/voicemail, or connected but
  under 20s): recorded with `call_connected=false`, no transcript pulled,
  no score — nothing to judge conversation quality on.
- **No recording despite a connected call** (rare — a real 422 was
  observed live for a busy call, which is expected; a connected call with
  no recording would be unexpected): row persisted with
  `flagged_reason` set, no score, so it's still visible rather than silently
  dropped.

### Out of scope
- **Dashboard page** — not built this session. Data is queryable directly
  from `staff_call_quality` in the meantime.
- **Historical backfill** — the scan job only picks up calls within its
  rolling lookback window (24h) going forward; see the `lastMessageType`
  edge case above for why a full backfill needs a different approach.
- **Per-rep pre-configured list** — per Kes, no static sales/support rep
  roster is maintained; `rep_user_id` is whatever GHL's `userId` field
  reports on each call message.

## Acceptance criteria
1. Given a completed sales-rep call ≥20s with a real recording, when the
   scan job processes it, then a `staff_call_quality` row exists with
   `transcript_text` populated (native GHL transcript if one exists, else
   Whisper), `conversation_type` set, and a `quality_score` from the sales
   rubric.
2. Given the same, but the call is with a current student, then the
   support rubric is used instead, scoring `resolution` against the
   specific request the model identifies from the transcript — not a
   generic checklist.
3. Given none of the GHL routing fields, `enrollment_date`, or call history
   resolve `conversation_type`, when scored, then the AI transcript
   fallback runs and its result (including `"ai_inferred"` as the source)
   is persisted.
4. Given a call classified `"other"` (Business or Partner) or left
   `"unknown"` after all signals including the AI fallback, when
   processed, then no rubric score is produced — `quality_score` stays
   null.
5. Given the same `ghl_message_id` is seen twice (job re-run, or a
   conversation reappearing in the search window), when the scan runs,
   then no duplicate row is created (pre-check query + DB unique
   constraint as backstop).
6. Given `STAFF_CALL_QUALITY_SCAN_ENABLED` is unset/false (the default),
   when the job fires on its schedule, then it no-ops — no GHL calls, no
   OpenAI calls, no rows written.

## Constraint architecture
- **Must** default `STAFF_CALL_QUALITY_SCAN_ENABLED=false`. Deploying this
  code must not silently start scanning every call location-wide and
  spending OpenAI transcription/scoring credits without Kes explicitly
  opting in after reviewing rubric output — this is a real, ongoing
  dollar cost, not a one-time action.
- **Must** use `GHL_CONVERSATIONS_API_KEY` (via `api_key_override`) for all
  Conversations reads — `GHL_API_KEY` does not have this scope and returns
  401 (confirmed live).
- **Must not** treat GHL's `lastMessageType=TYPE_CALL` filter as a complete
  call log — it is a discovery mechanism for a frequently-polled job, not
  a source of truth for "every call that happened."
- **Must not** score `"other"`/`"unknown"` conversation types — out of
  scope for both rubrics.
- **Must** keep all three duplicate GHL routing-picklist fields checked and
  their raw values persisted, per Kes — do not pick one preemptively.
- **Preference**: the transcript is the primary source for "what did the
  caller ask for" in the support rubric; GHL support-ticket fields are
  context only, since they can't be reliably tied to the specific call
  being scored.
- **Escalation trigger**: if GHL's transcription feature gets enabled on
  this account later (currently confirmed off/unavailable), re-verify
  before assuming `transcript_source="ghl_native"` becomes the common case
  — the code already prefers it when available, no change needed, but the
  cost/latency profile of the job would shift.

## Eval design
116 new unit tests across `test_ghl_adapter.py`, `test_openai_client.py`,
`test_call_classification.py`, `test_ghl_support_context.py`,
`test_call_quality_scoring.py`, `test_staff_call_quality_jobs.py`,
`test_campaigns.py`, `test_outbound_jobs.py`, `test_dashboard_v2.py`. Full
suite run before and after every addition confirms zero regressions
against the same 11 pre-existing, unrelated local-environment failures
(1145 passed as of the final run this session). Key live validations performed before any permanent code was
written: GHL contact search, conversations search, message list, recording
download, transcription lookup (confirmed unavailable), and a real
GHL-recording → Whisper transcription round trip — all against production
GHL data, via a temporary discovery script, not committed.

Not yet done: no live end-to-end run of the actual scan job against
production (recommended before flipping `STAFF_CALL_QUALITY_SCAN_ENABLED`
to true) — the individual pieces are each live-validated, but not yet
exercised together as the full pipeline outside of mocked unit tests.
