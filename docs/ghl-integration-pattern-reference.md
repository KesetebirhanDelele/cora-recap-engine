# GHL (GoHighLevel) Integration Pattern — Reference for Porting

**Purpose of this doc:** a read-only extraction of how `cora-recap-engine` talks to GoHighLevel,
for reuse in a different codebase's campaign-email pipeline. No code in this repo was changed to
produce this document.

**Important framing before you port anything:** this app's GHL integration is **call-centric, not
email-centric**, and it is **read/update-only on contacts — it never creates a GHL contact and
never searches by email.** Every contact this app touches already exists in GHL (leads originate
in GHL / its forms and get called by this system). Section 3 below documents this explicitly
because it changes what you can literally reuse vs. what you'll have to build fresh for a
"lookup by email/phone → create if missing" pipeline.

---

## 1. Auth / client setup

There are **three separate, independent GHL auth mechanisms** in this codebase, each with its own
credential, its own client class, and its own on/off switch. This separation is itself part of the
pattern worth porting: don't assume one API key covers contacts + tasks + conversations.

| # | Mechanism | Client class | Credential (env var) | Scopes used | Used for |
|---|-----------|---------------|----------------------|-------------|----------|
| 1 | Private Integration token (static Bearer) | `GHLClient` | `GHL_API_KEY` + `GHL_LOCATION_ID` | contacts, tasks, notes, custom fields | contact lookup by phone, field/task/note writes |
| 2 | Marketplace OAuth app (authorization-code + refresh) | `GhlConversationsClient` | `GHL_MARKETPLACE_CLIENT_ID` / `GHL_MARKETPLACE_CLIENT_SECRET` | Conversations write (`type="Call"`) via a registered Conversation Provider | logging call duration/status into Conversations |
| 3 | A **second** Private Integration token | `GhlInternalCommentClient` | `GHL_CONVERSATIONS_API_KEY` | `conversations.readonly`, `conversations/message.readonly`, `conversations/message.write` | posting call transcript + recording link as a staff-only InternalComment |

### Base URL / API version

All three clients hit the same base URL and the same version header — this is **GHL's v2 API**
(LeadConnector), not v1:

```python
# app/config/settings.py:85
ghl_base_url: str = "https://services.leadconnectorhq.com"
```

```python
# app/adapters/ghl.py:39
_VERSION_HEADER = "2021-07-28"
```

The version string is sent as a literal `Version` header (GHL v2's date-based versioning scheme),
not as part of the URL path:

```python
# app/adapters/ghl.py:78-84
def _headers(self) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {self.settings.ghl_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Version": _VERSION_HEADER,
    }
```

### Env vars (from `.env.example:60-96`)

```
GHL_BASE_URL=https://services.leadconnectorhq.com
GHL_API_KEY=
GHL_LOCATION_ID=
GHL_TIMEOUT_SECONDS=30
GHL_RETRY_MAX=3

GHL_WRITE_MODE=shadow
GHL_WRITE_SHADOW_LOG_ONLY=true
GHL_WRITE_CONTACT_FIELDS=false
GHL_WRITE_NOTES=false
GHL_WRITE_TASKS=false
```

Plus (declared in `app/config/settings.py:129-157`, not yet in `.env.example`):
`GHL_MARKETPLACE_CLIENT_ID`, `GHL_MARKETPLACE_CLIENT_SECRET`, `GHL_MARKETPLACE_SHARED_SECRET`,
`GHL_OAUTH_REDIRECT_URI`, `GHL_CONVERSATION_PROVIDER_ID`, `GHL_OAUTH_TARGET_LOCATION_ID`,
`GHL_CONVERSATIONS_API_KEY`, `GHL_WRITE_INTERNAL_COMMENT`, `GHL_WRITE_CONVERSATION_LOG`.

Config is loaded via **pydantic-settings** (`app/config/settings.py:36-43`) from a `.env` file;
every credential field is `Optional` so the app boots without them, and there are context-aware
`validate_for_ghl_reads()` / `validate_for_ghl_writes()` / `validate_for_ghl_marketplace_oauth()` /
`validate_for_ghl_internal_comment()` methods (`app/config/settings.py:355-412`) that raise a
`ConfigError` **at call time**, not at boot — e.g.:

```python
# app/config/settings.py:355-365
def validate_for_ghl_reads(self) -> None:
    """Raise ConfigError if minimum GHL read credentials are missing."""
    missing = []
    if not self.ghl_api_key:
        missing.append("GHL_API_KEY")
    if not self.ghl_location_id:
        missing.append("GHL_LOCATION_ID")
    if missing:
        raise ConfigError(
            f"GHL read integration requires: {', '.join(missing)}"
        )
```

### Shared client/wrapper module — retry, timeout, shadow-mode gate

There is no single shared HTTP wrapper class shared across all three clients, but all three
implement an **identical `_request()` method** (copy-pasted intentionally per their docstrings —
"Mirrors GHLClient._request()") wrapping a per-client `httpx.Client`:

```python
# app/adapters/ghl.py:68-74
def __init__(self, settings: Settings | None = None, _http: httpx.Client | None = None):
    self.settings = settings or get_settings()
    # _http injected in tests to avoid real network calls
    self._http = _http or httpx.Client(
        base_url=self.settings.ghl_base_url,
        timeout=self.settings.ghl_timeout_seconds,
    )
```

Retry policy — bounded exponential backoff, retried only on transient statuses:

```python
# app/adapters/ghl.py:38, 88-150
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

def _request(self, method, path, *, _retry_delay: float = 1.0, **kwargs) -> dict:
    for attempt in range(self.settings.ghl_retry_max + 1):
        try:
            resp = self._http.request(method, path, headers=self._headers(), **kwargs)

            if resp.status_code in _RETRYABLE_STATUS:
                if attempt < self.settings.ghl_retry_max:
                    time.sleep(_retry_delay * (2**attempt))
                    continue
                raise GHLError(
                    f"GHL request failed after {attempt + 1} attempts: HTTP {resp.status_code}",
                    status_code=resp.status_code,
                )

            resp.raise_for_status()
            return resp.json() if resp.content else {}

        except httpx.TimeoutException as exc:
            if attempt < self.settings.ghl_retry_max:
                time.sleep(_retry_delay * (2**attempt))
                continue
            raise GHLError(f"GHL request timed out after {attempt + 1} attempts: {method} {path}") from exc

        except httpx.HTTPStatusError as exc:
            # Non-retryable 4xx errors raise immediately
            body_snippet = exc.response.text[:300] if exc.response.content else ""
            raise GHLError(
                f"GHL HTTP error: {exc.response.status_code} {path} | {body_snippet}",
                status_code=exc.response.status_code,
            ) from exc

    raise GHLError(f"GHL request exhausted {self.settings.ghl_retry_max} retries: {method} {path}")
```

Key properties: retries on `429/500/502/503/504` and `httpx.TimeoutException`; delay doubles per
attempt (`base * 2**attempt`, base = 1.0s in production, 0.0 injected in tests); bounded by
`GHL_RETRY_MAX` (default 3); any other 4xx (e.g. 404, 422) raises `GHLError` immediately with no
retry (module docstring, `app/adapters/ghl.py:21-24`).

There is also a **write-mode safety gate** in front of every mutating call — a "shadow mode" that
logs the payload and returns a fake response instead of calling GHL, unless explicitly switched to
live (`app/adapters/ghl.py:8-13`, `356-465`). This is specific to this project's safety posture but
worth calling out as a portable idea: every write path checks a boolean before touching the network.

---

## 2. Lookup: does a contact already exist?

**There is no email search anywhere in this codebase.** The only contact-lookup method is by
phone number. Grepping the full `app/` tree for `search_contact_by_email` / any GHL email-search
call returns nothing — confirmed by direct search, not inferred.

### Search by phone

```python
# app/adapters/ghl.py:154-170
def search_contact_by_phone(self, phone: str) -> dict | None:
    """
    Search for a GHL contact by normalized E.164 phone number.

    Returns the first matching contact dict, or None if not found.
    Used during event enrichment to resolve contact_id from call data.
    Phone numbers are redacted from logs per security policy.
    """
    self.settings.validate_for_ghl_reads()
    logger.info("GHL search_contact_by_phone | phone=<redacted>")
    result = self._request(
        "GET",
        "/contacts/",
        params={"locationId": self.settings.ghl_location_id, "query": phone},
    )
    contacts = result.get("contacts", [])
    return contacts[0] if contacts else None
```

Endpoint: `GET /contacts/?locationId={location}&query={phone}` — GHL v2's generic contact search,
using the phone number as a free-text `query` param (there is no phone-specific search endpoint
used here).

### Response shape: found vs. not found

GHL always returns HTTP 200 with a `{"contacts": [...]}` envelope for this endpoint — there is no
404 for "no match." Found vs. not-found is distinguished purely by array length:

- **Found:** `{"contacts": [{...contact...}, ...]}` → the adapter returns `contacts[0]` (first
  match only — no de-dup/disambiguation logic if the query matches more than one contact).
- **Not found:** `{"contacts": []}` or the `contacts` key missing entirely → the adapter returns
  Python `None`.

Confirmed by the unit tests (`tests/unit/test_ghl_adapter.py:209-224`):

```python
def test_search_contact_returns_none_when_empty():
    ...
    mock_http.request.return_value = _mock_response(200, {"contacts": []})
    result = client.search_contact_by_phone("+15550000000")
    assert result is None

def test_search_contact_returns_none_when_key_missing():
    ...
    mock_http.request.return_value = _mock_response(200, {})
    result = client.search_contact_by_phone("+15550000000")
    assert result is None
```

A sample "found" contact shape from the same test file (`tests/unit/test_ghl_adapter.py:162-168`):

```python
_SAMPLE_CONTACT = {
    "id": "cid-abc",
    "phone": "+15551234567",
    "customFields": [
        {"id": "fid-001", "name": "AI Campaign Value", "fieldKey": "ai_campaign_value", "value": "0"},
    ],
}
```

### Resolution order / how ambiguity is handled

There is no "search both email and phone, then reconcile" logic. Every call site follows the same
two-branch pattern (repeated verbatim in `crm_jobs.py`, `conversation_log_jobs.py`,
`internal_comment_jobs.py`, `stale_recovery.py`):

1. If the local `contact_id` value looks like a **real GHL contact ID** (not all-digits after
   stripping spaces/dashes/`+`) → call `get_contact(contact_id)` directly (`GET /contacts/{id}`).
2. Otherwise (it looks like a bare phone number, or `contact_id` is empty) → fall back to
   `search_contact_by_phone(phone)` and use the resolved `id` from the result.

```python
# app/worker/jobs/crm_jobs.py:239-241
def _looks_like_phone(s: str) -> bool:
    stripped = s.replace(" ", "").replace("-", "").replace("+", "")
    return bool(stripped) and stripped.isdigit()
```

```python
# app/worker/jobs/crm_jobs.py:243-281 (abridged)
if effective_contact_id and not _looks_like_phone(effective_contact_id):
    try:
        ghl_contact = ghl.get_contact(effective_contact_id)
    except Exception as _read_exc:
        logger.warning("... GHL contact fetch failed (non-fatal) ...")
else:
    phone_to_search = contact_phone or (effective_contact_id or None)
    if phone_to_search:
        try:
            found = ghl.search_contact_by_phone(phone_to_search)
            if found:
                ghl_contact = found
                effective_contact_id = found.get("id")
        except Exception as _read_exc:
            logger.warning("... GHL phone search failed (non-fatal) ...")
```

This exists because inbound calls only carry a phone number (no GHL ID) until resolved; there's no
separate email identity to reconcile against, so there's no dual-search/duplicate-contact
resolution logic to port here — **you would need to design that yourself** for an
email-driven pipeline.

---

## 3. Create: what happens when no contact exists

**This app never creates a GHL contact.** There is no `create_contact()` method on any adapter, no
`POST /contacts/` call anywhere in `app/`, `execution/`, or `tests/` (confirmed by grep across the
whole tree for `create_contact` and `POST.*contacts`). Every write path (`update_contact_fields`,
`create_task`, `append_note`, the Conversations call-log, the InternalComment note) requires an
existing `contact_id` and simply **fails/skips** if one can't be resolved — it does not fall back
to creating one.

Concretely, in the two async jobs that log a call to Conversations, an unresolved contact is a hard
failure, not a create-then-continue:

```python
# app/worker/jobs/conversation_log_jobs.py:146-150
if not resolved_contact_id or not contact_phone:
    raise ValueError(
        f"Could not resolve a GHL contact with a phone on file | "
        f"call_event_id={call_event_id} effective_contact_id={effective_contact_id}"
    )
```

```python
# app/worker/jobs/internal_comment_jobs.py:160-165
if not resolved_contact_id:
    detail = f" | resolution_error={resolution_error}" if resolution_error else ""
    raise ValueError(
        f"Could not resolve a GHL contact | "
        f"call_event_id={call_event_id} effective_contact_id={effective_contact_id}{detail}"
    )
```

**Why:** leads in this system are sourced from GHL itself (forms/campaigns already create the
contact record in GHL before this app ever calls or emails them) — see
`directives/spec/16_ghl_integration.md` for the original integration contract. The system's job is
to read/update/annotate an existing contact, not onboard new ones.

**Implication for your port:** the "create if missing" half of the pattern you're asking about
(step 3 in your ask) **does not exist in this codebase to copy**. If your new pipeline needs to
create a GHL contact when lookup-by-email/phone comes up empty, you'll need to implement
`POST /contacts/upsert` (or `POST /contacts/`) yourself against GHL's v2 API — this repo has no
reference implementation, payload builder, ID-persistence-back-to-DB logic, or idempotency guard
for that operation. What you *can* reuse from here: the retry/timeout wrapper
(`_request()`, section 1), the shadow-write safety gate pattern, and the
phone-vs-real-ID disambiguation helper (`_looks_like_phone`, section 2).

The closest thing to "the GHL contact ID gets persisted onto a local record" is on the call/lead
side, not creation — `effective_contact_id` resolved from a phone search is used in-memory for
that job run and also written onto `LeadState.contact_id` / `CallEvent.contact_id` elsewhere in the
call-ingestion pipeline (see `app/models/lead_state.py`, `app/models/call_event.py`), but that's
populated from inbound webhook data, not from a create-contact response.

---

## 4. Update conversation/activity: logging a communication

This app logs **calls**, not emails, into GHL's Conversations feed — there are **two different,
independent write paths** for this, both fired for every completed call, both non-blocking of each
other:

### Path A — `type="Call"` via the OAuth Marketplace app (Conversations Provider)

```python
# app/adapters/ghl_conversations.py:202-259
def write_outbound_call(
    self,
    access_token: str,
    *,
    contact_id: str,
    to_phone: str,
    from_phone: str,
    call_duration_seconds: int,
    call_status: str = "completed",
) -> dict:
    payload = {
        "type": "Call",
        "contactId": contact_id,
        "conversationProviderId": self.settings.ghl_conversation_provider_id,
        "call": {
            "callDuration": call_duration_seconds,
            "callStatus": call_status,
            "to": to_phone,
            "from": from_phone,
        },
    }

    if not self.settings.ghl_write_conversation_log:
        return self._shadow_write("write_outbound_call", contact_id, payload)

    self.settings.validate_for_ghl_marketplace_oauth()
    return self._request(
        "POST",
        "/conversations/messages/outbound",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Version": _VERSION_HEADER,
        },
    )
```

Endpoint: `POST /conversations/messages/outbound`. Payload only carries duration/status/to/from —
**no transcript, no recording, no free-text body**. The module docstring
(`app/adapters/ghl_conversations.py:27-41`) records that call fields *must* nest under a `"call"`
object (flat top-level fields are silently ignored, not rejected) and that three different
recording-URL formats were all rejected with `"Invalid recording URL"` — attaching a
recording/transcript to this endpoint was investigated and abandoned, not simply unattempted.

### Path B — `type="InternalComment"` (second Private Integration token) — this is where the transcript/recording actually go

```python
# app/adapters/ghl_internal_comment.py:126-166
def write_call_note(
    self,
    *,
    contact_id: str,
    call_id: str,
    transcript: str,
    recording_url: str | None,
) -> dict:
    message = f"Transcript (call {call_id})\n\n{transcript.strip()}"
    if recording_url:
        message += f"{_SEPARATOR}Recording: {recording_url}"

    payload = {"contactId": contact_id, "type": "InternalComment", "message": message}

    if not self.settings.ghl_write_internal_comment:
        return self._shadow_write("write_call_note", contact_id, payload)

    self.settings.validate_for_ghl_internal_comment()
    return self._request(
        "POST",
        "/conversations/messages",
        json=payload,
        headers={
            "Authorization": f"Bearer {self.settings.ghl_conversations_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Version": _VERSION_HEADER,
        },
    )
```

Endpoint: `POST /conversations/messages` with `"type": "InternalComment"`. This is the call
**this app actually uses** to deliver full content (transcript + a plain-text recording link) —
confirmed working in both sandbox and production per the module docstring
(`app/adapters/ghl_internal_comment.py:12-13`, 2026-07-16). `InternalComment` is staff-only, never
visible to the contact — it deliberately sidesteps GHL's attachment-validation wall that blocks
real recording attachments on the `type="Call"` path.

Payload shape recap — **for porting to an email pipeline, `type="InternalComment"` with a
`message` string is the most directly reusable primitive**, since it accepts arbitrary text
(subject/body/preview all just concatenated into `message`) and doesn't require a registered
Conversation Provider the way `type="Call"` does:

| Field | Path A (`Call`) | Path B (`InternalComment`) |
|---|---|---|
| Full body | ✗ not supported | ✓ (`message`, any length up to GHL's limit) |
| Subject | ✗ | not modeled — would need to prepend it into `message` yourself |
| Direction (outbound/inbound) | implicit via `to`/`from` | not modeled — comment has no direction field |
| Timestamp | not sent explicitly (GHL stamps `dateAdded` server-side) | same |
| Message type | `"Call"` | `"InternalComment"` (staff-only; use `"Email"` or `"SMS"` if you want customer-visible messages — not used anywhere in this repo) |

Note: neither path is GHL's actual outbound-Email-conversation type — this repo never sends
`type="Email"` or `type="SMS"` through the Conversations API. Actual SMS/email *content* generation
in this app (`app/worker/jobs/crm_jobs.py:update_ghl_after_vm_message`, `channel_jobs.py`) is
delivered by writing the generated text into GHL **custom fields** (e.g. "Message", "Support Ticket
#2") for a GHL workflow/automation to pick up and send — not by calling a messaging endpoint
directly from this app. If your target pipeline wants to log an *actual* outbound email send as a
Conversations message, `type="Email"` is a real GHL Conversations message type but is **not
exercised anywhere in this codebase** — you'd be extrapolating from the `InternalComment`/`Call`
patterns shown here, not copying a working example.

### Sync or async?

**Always async, queued via Redis/RQ**, never inline with the call-processing request. Both
`write_conversation_log` and `write_internal_comment_note` are scheduled as jobs on a dedicated
`callbacks` queue immediately after call analysis completes, via a shared durable scheduler
(`app/worker/scheduler.py:schedule_job`) that also persists a Postgres row so the job survives a
Redis flush or worker restart:

```python
# app/worker/jobs/ai_jobs.py:493-520
def _schedule_conversation_log(session, call_id, call_event_id, contact_id, callbacks_queue, settings) -> None:
    from app.worker.jobs.conversation_log_jobs import write_conversation_log
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type="write_conversation_log",
        entity_type="call",
        entity_id=call_id or call_event_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={"call_id": call_id, "call_event_id": call_event_id, "contact_id": contact_id, "parent_job_id": None},
        rq_queue=callbacks_queue,
        rq_job_func=write_conversation_log if callbacks_queue is not None else None,
    )
```

`write_internal_comment_note` is scheduled the identical way
(`app/worker/jobs/ai_jobs.py:523-550`). Both jobs are **idempotent by dedupe row**: each checks for
an existing `status="created"` log row (`GhlConversationLogEvent` /
`GhlInternalCommentLogEvent`) keyed on `call_event_id` before writing, and skips cleanly if one
exists (`app/worker/jobs/conversation_log_jobs.py:90-102`,
`app/worker/jobs/internal_comment_jobs.py:94-107`) — this is the idempotency protection for
retries/duplicate enqueues, worth porting directly.

Same call, or separate for SMS vs. email? **Separate concept entirely** — this app has no
"log an SMS to Conversations" call at all. The only Conversations writes are call-shaped
(`type="Call"`, `type="InternalComment"`). SMS/email *content* is pushed via custom-field writes
(`update_contact_fields`), not via a Conversations message call.

---

## 5. Failure handling

### Retry policy (transport level)

Covered in full in Section 1 — bounded exponential backoff (`GHL_RETRY_MAX`, default 3) on
429/500/502/503/504 and timeouts; any other 4xx (404, 422, etc.) raises immediately with **no
retry**. Identical logic is copy-pasted into all three adapters
(`app/adapters/ghl.py:88-150`, `app/adapters/ghl_conversations.py:91-149`,
`app/adapters/ghl_internal_comment.py:79-122`) — there is no shared base class, despite the
identical shape; each raises its own error type (`GHLError`, `GhlConversationsError`,
`GhlInternalCommentError`), all a thin `RuntimeError` subclass carrying `status_code`.

No circuit breaker exists anywhere in this codebase (confirmed by grep for
"circuit" / "breaker" across `app/` — no hits). Timeout is a flat per-request `httpx` timeout
(`GHL_TIMEOUT_SECONDS`, default 30s), not a wrapped decorator.

### Job-level failure handling (application level)

Every GHL-writing worker job follows the same pattern: catch broad `Exception`, log it, record a
structured `Exception` row via `create_exception()` for the dashboard/alerting system, mark the RQ
job failed, then **re-raise** so RQ's own retry mechanism picks it up — except in shadow mode,
where retries are capped locally to avoid noise:

```python
# app/worker/jobs/crm_jobs.py:341-371
except Exception as exc:
    logger.exception(
        "create_crm_task: error | job_id=%s call_event_id=%s attempt=%d: %s",
        job_id, call_event_id, attempt_count, exc,
    )
    create_exception(
        session,
        type="crm_task_failed",
        severity="warning",
        context={
            "call_id": call_id,
            "call_event_id": call_event_id,
            "job_id": job_id,
            "error": str(exc),
            "attempt_count": attempt_count + 1,
        },
        entity_type="call",
        entity_id=call_id or call_event_id,
    )
    fail_job(session, job, reason=str(exc))
    session.commit()
    if not flags.ghl_writes_enabled and attempt_count >= 2:
        logger.warning("create_crm_task: shadow mode attempt limit reached (%d), not re-raising", attempt_count)
        return
    raise
```

`create_exception()` rows feed a dashboard "Exception Queue" (`app/worker/exceptions.py`,
`app/api/routes/exceptions.py`) — this is the alerting surface; there's no separate
Slack/PagerDuty push directly from the GHL adapter layer itself, it's all funneled through this one
DB-backed exception system, which a separate alerting service polls
(`app/services/alerting.py`) to fire email/webhook alerts on thresholds.

**Read-path failures degrade gracefully instead of raising.** Contact lookup failures during
enrichment are treated as non-fatal — logged as a warning, and the caller proceeds with whatever
context it already has (no contact fields resolved, field updates skipped) rather than aborting
the whole job:

```python
# app/worker/jobs/crm_jobs.py:263-281
if phone_to_search:
    try:
        found = ghl.search_contact_by_phone(phone_to_search)
        if found:
            ghl_contact = found
            ...
    except Exception as _read_exc:
        logger.warning(
            "create_crm_task: GHL phone search failed (non-fatal) | phone=%s: %s",
            phone_to_search, _read_exc,
        )
```

**Write-path failures where the contact can't be resolved at all are hard failures** (raise
`ValueError`, caught by the outer handler above) — shown in Section 3's `write_conversation_log`
and `write_internal_comment_note` snippets. So the failure posture is deliberately asymmetric:
*enrichment* reads fail soft, but a *write* job with no resolvable contact fails loud (exception +
alert-queue row), since a silently-dropped call-log write would be a data-loss bug, not just a
missing nice-to-have field.

### Skip vs. fail distinction (both jobs document this explicitly)

```python
# app/worker/jobs/conversation_log_jobs.py:16-22
Skip conditions (not errors, complete_job cleanly):
  - OAuth app not installed on this location yet (no stored token)
  - Dedupe: a 'created' ghl_conversation_log_events row already exists

Failure conditions (create_exception, fail_job, non-fatal to call processing):
  - Missing call_event_id / CallEvent not found
  - GHL contact could not be resolved (no phone match)
```

OAuth-not-installed is deliberately treated as a clean skip, not a failure
(`app/worker/jobs/conversation_log_jobs.py:159-166`), since it's an expected steady-state (a
location that hasn't completed the Marketplace install yet), not an error condition.

---

## Summary: what's directly portable vs. what you must build

| Piece | Portable as-is | Notes |
|---|---|---|
| Retry/backoff/timeout wrapper (`_request()`) | ✅ | Copy the shape from Section 1 |
| Shadow-mode write gate | ✅ | Good safety pattern for a new campaign pipeline too |
| Phone-vs-ID disambiguation (`_looks_like_phone`) | ✅ (if you also key by phone) | N/A if you're purely email-keyed |
| Contact search by phone (`GET /contacts/?query=`) | ✅ pattern, adapt field | Same endpoint works for email as free-text `query` — untested here though |
| Contact search by email | ❌ does not exist here | Build fresh — likely same `GET /contacts/` with `query=email` |
| Create contact if missing | ❌ does not exist here | Build fresh — likely `POST /contacts/upsert` |
| Idempotency dedupe-row-before-write pattern | ✅ | Section 4's `GhlConversationLogEvent`/`GhlInternalCommentLogEvent` pattern |
| Async job scheduling + durable Postgres-backed retry | ✅ | `schedule_job()` — decouples the write from the request/response cycle |
| Logging outbound *email* as a Conversations message | ❌ not exercised | Nearest working reference is `type="InternalComment"` (Section 4) — `type="Email"` would need fresh validation against GHL's sandbox, same way `type="Call"` was |
| Exception/alerting funnel | ✅ pattern | `create_exception()` → dashboard queue → alert thresholds |
