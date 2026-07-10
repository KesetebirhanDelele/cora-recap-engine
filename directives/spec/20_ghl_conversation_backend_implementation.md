# spec/20_ghl_conversation_backend_implementation.md

## Implementation status

| Area | Status |
|---|---|
| Marketplace app + Conversation Provider (dashboard config) | IMPLEMENTED — see `spec/19` |
| OAuth callback route (`GET /oauth/callback`) | IMPLEMENTED — `app/api/routes/ghl_oauth.py` |
| `ghl_oauth_tokens` + `ghl_conversation_log_events` tables + migration | IMPLEMENTED — migration 0019, applied to Hetzner prod Postgres 2026-07-10 |
| Token refresh routine | IMPLEMENTED — `get_valid_access_token()`, `app/services/ghl_oauth.py` |
| `app/adapters/ghl_conversations.py` — OAuth-based write client | IMPLEMENTED, including Company→Location conversion |
| `write_outbound_call()` (call duration/status → GHL) | IMPLEMENTED, no attachment/transcript (see §7) |
| `write_conversation_log` job, wired into call-completion pipeline | IMPLEMENTED — `app/worker/jobs/conversation_log_jobs.py` |
| Transcript write — feasibility unconfirmed | NOT STARTED (spike required first) |
| Conversation Provider webhook receiver (Delivery URL) | NOT STARTED — build only if verification shows GHL requires it |
| Acceptance Criterion 12 (sandbox end-to-end, visual confirmation) | **PASSED 2026-07-10** — see §8 |
| Real install on production Colaberry GHL location | UNBLOCKED — not yet done, pending explicit go-ahead |

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

- Is the Conversation Provider Delivery URL actually invoked for a `Call`-type, custom, outbound-only provider, or only for providers that also need to receive inbound messages? Affects whether Decomposition step 7 is needed at all.

---

## 8. Acceptance Criterion 12 — passed (2026-07-10)

Two real bugs surfaced during live end-to-end testing against the Hetzner deployment, both now fixed
(commits `21f73e5`, `ecad749`/`fc7eee9` on `feat/ghl-call-conversation-sync`):

1. **`get_location_token()` was missing the required `Version: 2021-07-28` header** on
   `POST /oauth/locationToken`, causing every real install to fail with `401 "version header was
   not found"` right after the initial code exchange succeeded. The throwaway probe script that
   originally confirmed this endpoint's shape included the header manually; it never made it into
   the production adapter method. Fixed by adding a shared `_VERSION_HEADER` constant, used by both
   `get_location_token()` and `write_outbound_call()`.

2. **GHL Marketplace test installs do not necessarily reuse the same company/agency across
   sessions.** A fresh, cookie-less incognito authorization can land on a *different* GHL company
   each time (observed two different `companyId` values across attempts), and that company will not
   own whatever `GHL_OAUTH_TARGET_LOCATION_ID` was configured for a previous session's sandbox — GHL
   correctly rejects the locationToken exchange with `400 "Location does not belong to the
   company"`. There is no code fix for this; it's an operational gotcha. When re-testing later:
   confirm which company/agency is shown on the consent screen, verify (or create) a sub-account
   under *that* company, and update `GHL_OAUTH_TARGET_LOCATION_ID` to match before retrying.

Also discovered (unrelated to the above, tracked as a separate follow-up, not yet fixed): **`logger.info()` calls are silently dropped app-wide.**
`settings.log_level` is only ever passed to `uvicorn.run(..., log_level=...)`, which configures
uvicorn's own logger, not Python's root logger — `logging.basicConfig()` (or equivalent) is never
called anywhere in the app. Python's default `lastResort` handler only surfaces WARNING and above,
so every `logger.info(...)` in the codebase (adapters, services, jobs) currently produces no output
at all in `docker compose logs`. Only `logger.error`/`logger.warning` are currently visible. Worth a
dedicated fix — see the "Fix app-wide logging configuration gap" item — since it silently defeats a
lot of existing observability, not just this feature's logging.

Once the above were resolved, `write_outbound_call()` was verified using the **real production code
path** (`get_valid_access_token()` + `GhlConversationsClient.write_outbound_call()`, not a probe
script) against a fresh sandbox sub-account (location `eWe9cRDf0UmSSIBxBMAO`): a token was stored in
`ghl_oauth_tokens`, a real `POST /conversations/messages/outbound` call returned `201 success:true`
with a real `conversationId`/`messageId`, and Kes visually confirmed the resulting "Outbound Call"
activity entry in that contact's Conversations tab in the GHL UI.

**Remaining before this feature is fully done**: the app-wide logging gap (optional, not blocking),
and the real install against the production Colaberry GHL location (Decomposition item, explicitly
gated on Kes's go-ahead per this spec's Musts).

---

## 7. Confirmed write schema (spike complete, 2026-07-09)

Verified against the `Cora Sandbox` GHL test account (location `MdXLDwpyhdQ8iAoDnEOC`) with a real
location-level OAuth token, using `POST /conversations/messages/outbound`. GHL's validation errors
were specific enough to iterate the exact schema field-by-field.

### Auth gotcha: this app issues a Company-level token, not Location-level

Despite the Marketplace app being configured with Target User = Sub-Account, exchanging the
authorization code from the sandbox install returned `userType: "Company"`, `locationId: null`.
The Conversations write endpoint rejects Company-level tokens (`401 "This authClass type is not
allowed to access this scope"`). A second exchange is required:

```
POST https://services.leadconnectorhq.com/oauth/locationToken
Content-Type: application/x-www-form-urlencoded
Authorization: Bearer {company_access_token}
Body: companyId={companyId from step 1}, locationId={target location id}
```

This returns a proper `userType: "Location"` token scoped to that location, valid for the write
endpoint. **`get_valid_access_token()` (app/services/ghl_oauth.py) does not currently do this
second step** — it stores and returns whatever token `exchange_code_for_token()` /
`refresh_access_token()` return directly. This must be added before Step 4/5 implementation:
after the initial code exchange, if `userType == "Company"`, immediately perform the
`/oauth/locationToken` exchange and store *that* token (with its own `expires_in`) instead of (or
in addition to) the company token. Refreshing a location-level token later returns another
location-level token directly (no repeated two-step dance needed — confirmed the refresh response
also carries `userType`/`locationId`).

### Confirmed working request body

```json
{
  "type": "Call",
  "contactId": "<real GHL contact id — must be resolved first, not a phone string>",
  "conversationProviderId": "6a4eebb1f41b5b39ff760caf",
  "attachments": ["<recording URL — see caveat below>"],
  "call": {
    "callDuration": 104,
    "callStatus": "completed",
    "to": "<contact's phone, E.164 — MUST exactly match the contact's phone on file>",
    "from": "<business/caller phone, E.164>"
  }
}
```

Response on success: `HTTP 201 {"success": true, "conversationId": "...", "messageId": "..."}`.

Confirmed via GHL's own validation errors, in order encountered:
- `contactId` (or `conversationId`) is required at the top level.
- Call-specific fields (duration/status/to/from) must be nested under a **`call`** object — a
  flat `callDuration`/`callStatus` at the top level is silently accepted-but-ignored by the API
  (no error), which is exactly the kind of silent-failure risk this spec warned about — **the
  `call` nesting is not optional**, don't flatten it.
- `call.to` is required, must be a validly-formatted E.164 number, **and must match the target
  contact's phone number on file** — GHL rejects a well-formatted number that doesn't match the
  contact (`CONVERSATIONS_MSG_INVALID_PHONE`). Implication: the job must fetch/confirm the
  contact's actual phone before writing, not just pass whatever phone string Cora has locally —
  use the contact record returned by the phone-resolution step (crm_jobs.py pattern, §1) as the
  source of truth for `call.to`, not `CallEvent`'s raw payload phone field.
- `call.from` was accepted without further validation in testing (no equivalent contact-matching
  check observed) — treat as the Synthflow/business caller number.

### Attachments (recording) — needs the file-upload endpoint, not a raw URL

- `attachments` **must be a top-level field** (array of strings), not nested inside `call`, and
  not named `recording`/`recordingUrl` — those are silently ignored (message creates successfully
  but `GET .../recording` returns `422 "Message does not have recording"`).
- However, two different externally-hosted audio URLs (one `.mp3`, one `.wav`, both directly
  fetchable in a browser) were both rejected with `422 "Invalid recording URL"` when placed in
  `attachments`.
- Followed the lead: spiked GHL's own **"Upload file attachments" endpoint**
  (`POST /conversations/messages/upload`, multipart, field name `fileAttachment`, plus
  `contactId`). This worked — `201`, response shape `{"uploadedFiles": {"<filename>":
  "<hosted_url>"}, "traceId": "..."}` — and returned a real GHL-hosted URL:
  `https://static-assets.internal.usercontent.site/conversations-assets/location/{locationId}/conversations/contact/{contactId}/{uuid}.wav`.
  **Using that exact GHL-hosted URL in `attachments` on the outbound-call write still returned the
  same `422 "Invalid recording URL"`.** Even GHL's own upload endpoint's output fails GHL's own
  recording validation for a `Call`-type message specifically.
- **Conclusion: do not build attachment/recording support into `write_outbound_call()` yet.**
  Three attempts (two external URLs, one GHL-hosted URL from the sanctioned upload endpoint) all
  failed the same way — this is no longer a "wrong field shape" problem, it's either (a) `Call`-type
  messages need a completely different attachment mechanism than the generic
  `attachments: [url]` pattern (e.g. an object shape with `type`/`filename`/`sizeBytes`, per the
  generic-message docs referenced in spec/19 — not yet tried for `type: "Call"` specifically), or
  (b) a genuine platform limitation/quirk worth raising with GHL support directly. Not worth
  further guessing against the sandbox — **ship call metadata (duration/status/to/from) without
  a recording first**, track attachment support as an explicit, separately-scoped follow-up.

### Transcript — still unresolved, do not assume a field name

- Guessed a top-level `transcript` field (arbitrary string) and a `GET
  /conversations/messages/{id}/locations/{id}/transcription` read endpoint by analogy with the
  recording endpoint's URL shape. The write was silently accepted (unknown field ignored, same
  risk pattern as before) and the read endpoint returned a **generic framework 404 ("Cannot GET
  ...")**, not a structured "no transcription" business error — meaning the guessed path itself is
  wrong, not that transcripts are confirmed unsupported. This remains exactly the open question
  spec/19 originally raised. Do not add a transcript field to `write_outbound_call()` until the
  correct write mechanism (if any) is found — ship recording + call metadata first, treat
  transcript as a separate follow-up spike.

### Updated Decomposition (supersedes §4 steps 4–6)

4a. **DONE** (2026-07-09) — Company→Location token exchange added to
    `complete_oauth_install()` / `app/services/ghl_oauth.py`, wired into the `/oauth/callback`
    route. Unit-tested.
4b. **DONE, negative result** — spiked the "Upload file attachments" endpoint; it works, but its
    output is not accepted by the outbound-call write's `attachments` field (see above). Attachment
    support is not unblocked by this — do not attempt 4c's attachment path yet.
4c. Implement `write_outbound_call()` using the confirmed schema above (§7) **without
    `attachments`** — `type`, `contactId`, `conversationProviderId`, and the nested `call` object
    (`callDuration`, `callStatus`, `to`, `from`). This alone delivers the primary win (call
    duration/status/timestamp visible in GHL Conversations) and is fully unblocked.
5. Job wiring — unchanged from §4 step 5. Trigger after call completion, shadow-gated, dedupe via
   `ghl_conversation_log_events`, contact-phone resolution before write (§1 pattern), using
   `call.to` = the *resolved contact's* phone (not `CallEvent`'s raw payload phone — see §7).
6. Attachment (recording) delivery — deferred. Needs either: trying an object-shaped attachments
   entry (`{"type": ..., "url": ..., "filename": ...}`) specifically for `type: "Call"` messages
   (not yet attempted), or contacting GHL support about why their own upload endpoint's output
   fails their own recording validation. Not blocking for the primary deliverable.
7. Transcript — deferred to a dedicated future spike once a correct candidate field/endpoint is
   found (e.g. from GHL support, changelog, or a differently-shaped guess); not blocking for
   call-metadata delivery, which is the primary goal per spec/19.
