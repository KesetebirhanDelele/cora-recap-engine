# spec/20_ghl_conversation_backend_implementation.md

## Implementation status

| Area | Status |
|---|---|
| Marketplace app + Conversation Provider (dashboard config) | IMPLEMENTED — see `spec/19` |
| OAuth callback route (`POST /oauth/callback` or similar) | NOT STARTED |
| `ghl_oauth_tokens` table + migration | NOT STARTED |
| Token refresh routine | NOT STARTED |
| `app/adapters/ghl_conversations.py` — OAuth-based write client | NOT STARTED |
| `write_call_conversation` job (recording URL + duration/status → GHL) | NOT STARTED |
| `ghl_conversation_log_events` dedupe table | NOT STARTED |
| Transcript write — feasibility unconfirmed | NOT STARTED (spike required first) |
| Conversation Provider webhook receiver (Delivery URL) | NOT STARTED — build only if verification shows GHL requires it |
| Real install on production Colaberry GHL location | BLOCKED on all of the above |

---

## 1. Self-contained problem statement

### Business goal

After a Synthflow call completes and Cora processes it, staff currently have no way to listen to
the recording or read the transcript from inside GHL — they'd have to go to Cora's own dashboard.
The goal is to push the call recording (as a linked attachment) and duration/status into the
contact's **Conversations** activity tab in GHL automatically, so support/admissions staff can
review calls without leaving their CRM. Transcript delivery is a stretch goal, gated on API
feasibility (see Constraint 4 below).

This is **additive** — it does not replace or modify the existing Private-Integration-based writes
documented in `spec/16` (contact fields, tasks, notes). Both mechanisms coexist.

### Relevant systems and files

- `app/models/call_event.py` — `CallEvent.recording_url` (Text, nullable) and
  `CallEvent.transcript` (Text) are **already populated** at ingestion time in
  `app/worker/jobs/call_processing.py:238` from the Synthflow webhook payload. No new data capture
  is needed — the gap is entirely on the "write it to GHL" side.
- `app/worker/jobs/crm_jobs.py` — existing pattern to follow for a new GHL-write job: claim/complete/fail
  lifecycle (`app/worker/claim.py`), shadow-gate check, `create_exception` on failure,
  `_resolve_to_field_ids`-style helper pattern (though this new path doesn't need field-ID
  resolution — it's message creation, not a contact field write).
- `app/worker/main.py` — `_JOB_QUEUE_ATTRS` and `get_job_registry()` must both register the new job
  type or the scheduler loop will starve it (see `spec/16` alert-system notes — this has bitten this
  repo before with `update_ghl_after_vm_message`).
- `app/adapters/ghl.py` — existing `GHLClient`, Private-Integration-only. **Do not extend this
  class for OAuth.** Auth mechanics are fundamentally different (static Bearer token vs. per-location
  access/refresh tokens with expiry) — conflating them risks silently breaking the working
  Private-Integration write paths. Build a separate `app/adapters/ghl_conversations.py`.
- `app/worker/jobs/crm_jobs.py::create_crm_task()` (~line 201–247) — existing inline pattern for
  resolving a phone-derived `contact_id` to a real GHL contact ID before any GHL write: a
  `_looks_like_phone()` check, then `GHLClient.search_contact_by_phone()` as fallback. This is
  **inline in the job function, not an extracted reusable helper** — earlier project notes claimed
  a `_resolve_ghl_contact_id()` helper in a `stale_recovery.py` service; that code exists only on
  the unmerged `feat/production-deployment-hardening` branch, not on `main`. Since this work is
  based on `main`, replicate the inline pattern from `create_crm_task()` (or extract it into a
  shared helper as part of this work — reasonable either way, but don't assume the helper already
  exists).
- `app/config/settings.py` — existing `ghl_write_mode` / `ghl_writes_enabled` shadow-gate pattern
  (lines ~116, ~300–305). New settings should follow the same naming convention:
  `ghl_marketplace_client_id`, `ghl_marketplace_client_secret`, `GHL_CONVERSATION_PROVIDER_ID`
  (`6a4eebb1f41b5b39ff760caf` per `spec/19`), and a new shadow flag `GHL_WRITE_CONVERSATION_LOG`.

### Input data

- `CallEvent.recording_url`, `CallEvent.transcript`, `CallEvent.duration_seconds`, `CallEvent.contact_id`
  (phone-derived, per existing pattern — must be resolved to a real GHL contact ID)
- GHL OAuth tokens per location (new data, doesn't exist yet)
- `conversationProviderId` = `6a4eebb1f41b5b39ff760caf` (static, from `spec/19`)

### Expected outputs

- One GHL Conversations message per completed call, visible in the contact's Conversations tab,
  attributed to the "Cora Voice Calls" provider, with the recording as a playable/downloadable
  attachment and correct call duration/status.
- Transcript included **only if** the spike in Constraint 4 confirms GHL's API accepts a
  caller-supplied transcript at write time. If not, this is explicitly out of scope and the doc
  should be updated to say so — not silently attempted and left broken.

### Known edge cases

1. `recording_url` is null/blank (e.g., voicemail-only or a call that failed before recording) → write duration/status without an attachment, don't fail the job.
2. `transcript` is blank → same call-blank-transcript guard pattern already used elsewhere in `app/services/ai.py` — skip transcript, don't fail.
3. Contact resolution fails (phone search returns nothing) → non-fatal, `exceptions` row, no crash.
4. OAuth access token expired at write time → transparently refresh via refresh_token before retrying the write once.
5. Refresh token itself expired/revoked (location uninstalled the app, or 1yr elapsed) → this is NOT silently retryable — raise a critical alert (mirrors `ghl_auth_failure` in `app/services/alerting.py`), since it requires a human to reinstall/reauthorize.
6. Job retried by RQ after a crash mid-write → must not create a duplicate Conversations message (idempotency, see Constraint 2).
7. GHL API 429/5xx → bounded retry with backoff, same policy shape as `GHLClient._request()` in `spec/16`.
8. A location that has never completed the OAuth install (e.g. any location besides the one production Colaberry account, or the account before install) → shadow-gate / skip cleanly, not an error — this job should be safe to run globally even before every location has connected the app.

### Out of scope

- Fixing the `assign_to` blank-LLM-output bug surfaced in a separate debugging session — explicitly deferred, unrelated to this work.
- Inbound call handling — Cora is outbound-only (`launch_new_lead_call` via Synthflow Make Call webhook); no inbound Conversation Provider message type is needed.
- Replacing GHL's native calling/phone channel — deliberately avoided by registering as a **custom** Conversation Provider (`spec/19`).
- Real install against the production Colaberry GHL location — happens only after this spec's acceptance criteria pass against the sandbox.

---

## 2. Acceptance criteria

1. Given `GHL_WRITE_CONVERSATION_LOG=false` (default), when the call-log job runs for any call, then no GHL API call is made and a shadow dict is logged — mirrors existing shadow-gate behavior exactly.
2. Given `GHL_WRITE_CONVERSATION_LOG=true`, a valid non-expired location access token, and a `CallEvent` with a non-null `recording_url`, when the job runs, then exactly one `POST /conversations/messages/outbound` call is made with `conversationProviderId=6a4eebb1f41b5b39ff760caf`, `callDuration`, `callStatus`, and an `attachments` entry containing the recording URL.
3. Given a `CallEvent` with `recording_url IS NULL`, when the job runs, then the write still happens (duration/status only, no attachment) — it does not fail or skip.
4. Given the location's access token is expired, when the job attempts a write, then it refreshes the token via the stored refresh_token first, persists the new token pair, and completes the write with the refreshed token — all within the same job execution (no manual intervention, no extra job needed).
5. Given the location's refresh token is invalid/expired, when a refresh attempt fails, then an `exceptions` row is created with a distinct type (e.g. `ghl_oauth_reauth_required`) and severity `critical`, and no further retries are attempted for that location until a human reinstalls.
6. Given a job retry for the same `call_event_id` (RQ redelivery after crash, or manual dashboard retry), when the job runs again, then no second Conversations message is created — enforced via a new dedupe table keyed on `call_event_id` (same shape as `task_events` in `spec/16`).
7. Given contact resolution fails (phone search returns no match), when the job runs, then an `exceptions` row is created (type: `conversation_log_failed`) and the job completes without crashing the worker.
8. Given a transient GHL 429/5xx, when the write is attempted, then it retries with bounded exponential backoff and raises after exhaustion, consistent with `spec/16`'s retry contract.
9. Given the location has no stored OAuth tokens at all (app not yet installed there), when the job runs, then it exits cleanly as a shadow/no-op (logged, not an exception) — this must not break call processing for locations where the OAuth app isn't installed yet.
10. Given the transcript-write feasibility spike (see Constraint 4) concludes the API does **not** support caller-supplied transcripts, then the implementation must not attempt to send one, and `spec/19`'s open question must be updated with the finding — not left stale.
11. All new code paths have unit tests with GHL HTTP calls mocked, following the existing test patterns in `tests/unit/` for `crm_jobs.py` / `test_webhook_recovery_jobs.py`.
12. Before any write is attempted against the real production Colaberry location, the full flow (1–9 above) must first pass against the `Cora Sandbox` GHL test account created in `spec/19`, with a human visually confirming the message appears correctly in that sandbox account's Conversations tab.

---

## 3. Constraint architecture

### Musts

- Must reuse the shadow-gate pattern: a new `GHL_WRITE_CONVERSATION_LOG` flag, default `false`, following the exact naming/behavior convention of the existing `GHL_WRITE_*` flags in `spec/16`.
- Must resolve `contact_id` to a real GHL contact ID before any write, reusing the phone-search pattern already in `create_crm_task()` (see §1) — do not silently write to a phone-string contact_id.
- Must store OAuth tokens server-side only (new Postgres table), never log them, never expose them via any API/dashboard response.
- Must register the new job type in **both** `_JOB_QUEUE_ATTRS` and `get_job_registry()` in `app/worker/main.py` — this exact omission has previously caused a job type to silently starve (`spec/16` alert-system notes).
- Must be idempotent under retry (dedupe table, per this repo's global concurrency rules).
- Must run against the `Cora Sandbox` test account only until acceptance criterion 12 passes — never point dev/test runs at the real production location.

### Must-nots

- Must not modify or extend `app/adapters/ghl.py` (`GHLClient`) — the OAuth client is a new, separate adapter.
- Must not touch the existing Private-Integration write paths (Path 1–4 in `spec/16`) in any way.
- Must not run the GHL write inline inside the main call-processing job — it must be a separate async job on the `callbacks` queue (or a new dedicated queue if volume warrants it later), consistent with `create_crm_task`.
- Must not commit `.env`, token values, or the Client Secret to git.
- Must not silently retry forever on a revoked refresh token — that must escalate (Acceptance Criterion 5).

### Preferences

- Prefer a new file `app/worker/jobs/conversation_log_jobs.py`, mirroring `crm_jobs.py`'s structure.
- Prefer a single new migration adding both the token table and the dedupe table together, since they ship as one feature.
- Prefer lazy token refresh (check expiry at write time) over a separate scheduled refresh job, to keep this feature's footprint small — revisit only if token expiry causes observed write failures in practice.

### Escalation triggers

- If the transcript-write spike (Constraint/Acceptance Criterion 10) shows GHL's API silently drops or ignores a supplied transcript rather than erroring, stop and confirm with Kes before shipping anything that implies transcript delivery works when it doesn't.
- If GHL's Conversation Provider Delivery URL turns out to be a hard requirement for the provider to remain active (not just for receiving inbound events we don't need), escalate before under-building it — this needs a real check against GHL's behavior, not an assumption either way.

---

## 4. Decomposition / break pattern

1. **Migration + token model** — `ghl_oauth_tokens` (location_id PK, access_token, refresh_token, expires_at, created_at, updated_at) and `ghl_conversation_log_events` (call_event_id PK/unique, ghl_message_id, status, created_at). Verifiable in isolation: migration applies cleanly, model round-trips.
2. **OAuth callback route** — receives `code`, exchanges via GHL's token endpoint, upserts into `ghl_oauth_tokens`. Verifiable: manually run the sandbox OAuth flow from `spec/19` end-to-end and confirm a row lands in the table.
3. **Token refresh helper** — `get_valid_access_token(location_id)` — checks expiry, refreshes if needed, persists. Verifiable: unit test with a pre-expired token, mocked refresh endpoint.
4. **`app/adapters/ghl_conversations.py`** — `write_outbound_call(location_id, contact_id, recording_url, duration_seconds, call_status)`, using the refreshed token, `conversationProviderId`, and the same bounded-retry policy shape as `GHLClient._request()`. Verifiable: unit tests with mocked HTTP, one manual call against the sandbox.
5. **`conversation_log_jobs.py` job + wiring** — new job type, registered in `_JOB_QUEUE_ATTRS`/`get_job_registry()`, scheduled after call completion (same trigger point as `create_crm_task`), shadow-gated, dedupe-checked. Verifiable: unit tests for routing + dedupe; one full run against the sandbox account with a visible message in its Conversations tab.
6. **Transcript feasibility spike** — before or alongside step 4, make one manual API call against the sandbox with a transcript-like field in the payload and inspect the response / check the Conversations UI to see if it's accepted, ignored, or rejected. Document the finding in `spec/19`. This determines whether step 4/5 include transcript at all.
7. **(Conditional) Delivery URL webhook receiver** — only build if step 6's research, or direct testing, shows GHL requires a reachable Delivery URL for the provider to function for outbound-only use. Otherwise leave as a documented placeholder.

Each chunk should be a separate commit; steps 1–3 and 6 can happen in parallel since they don't depend on each other.

---

## 5. Evaluation design

### Unit tests (fast, mocked, required before any chunk is considered done)

- Token refresh: valid token → no refresh call made; expired token → refresh called, new token persisted; refresh failure → `exceptions` row created, no infinite retry.
- Write payload construction: with recording URL, without recording URL (no attachment key), with/without transcript depending on spike outcome.
- Shadow-gate: `GHL_WRITE_CONVERSATION_LOG=false` → no HTTP call, shadow dict returned.
- Dedupe: second job run for the same `call_event_id` → no second write attempted.
- Contact resolution failure → `exceptions` row, job completes (not failed/crashed).
- Retry/backoff on 429/5xx → matches `spec/16`'s existing retry-policy test shape.
- Job registry: new job type present in both `_JOB_QUEUE_ATTRS` and `get_job_registry()` (regression guard against the starvation bug class noted in `spec/16`).

### Integration test (opt-in, sandbox-only, manual verification step)

- Full round trip against `Cora Sandbox`: real OAuth token in the test DB → job runs → real `POST` to GHL → human confirms the message + recording attachment appear correctly in the sandbox account's Conversations UI. This is Acceptance Criterion 12 and cannot be fully automated (the "does it look right in the UI" check is inherently manual), but the API call itself should assert a 200/201 and a returned message ID.

### Regression checks

- All existing `spec/16` tests continue to pass unmodified.
- `app/adapters/ghl.py` has zero diff from this work (confirms no accidental coupling).

---

## 6. Open questions carried over from spec/19

- Does GHL's conversation-message write API accept a caller-supplied transcript, or are transcripts only ever GHL-generated from the recording? Must be resolved by the spike in Decomposition step 6 before claiming transcript delivery as done.
- Is the Conversation Provider Delivery URL actually invoked for a `Call`-type, custom, outbound-only provider, or only for providers that also need to receive inbound messages? Affects whether Decomposition step 7 is needed at all.
