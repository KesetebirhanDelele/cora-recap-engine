# spec/16_ghl_integration.md

## Implementation status

| Area | Status |
|---|---|
| Auth (Bearer JWT + Version header) | IMPLEMENTED |
| Retry policy (429/5xx + timeout, bounded exponential backoff) | IMPLEMENTED |
| Shadow gate (`GHL_WRITE_MODE`, `ghl_writes_enabled`) | IMPLEMENTED |
| Read ops: `search_contact_by_phone`, `get_contact` | IMPLEMENTED |
| Field resolution: `resolve_field_id`, `get_field_value` | IMPLEMENTED |
| Write op: `create_task` | IMPLEMENTED (shadow-gated) |
| Write op: `update_contact_fields` (student summary delivery) | IMPLEMENTED (shadow-gated) |
| Write op: `append_note` | IMPLEMENTED (shadow-gated) |
| CRM task dedupe via `task_events` | IMPLEMENTED |
| Student summary consent gate | IMPLEMENTED |
| Audit log on summary delivery | IMPLEMENTED |
| Voicemail finalization field writes | PLANNED — not yet live |
| `LAST_CALL_STATUS` field write | PLANNED — not yet live |
| `MARK_AS_LEAD` field write | PLANNED — not yet live |
| `NOTES` field write (transcript/summary to notes) | PLANNED — not yet live |

---

## Purpose

GoHighLevel (GHL) / LeadConnector is the CRM authority for contact records, custom fields, tasks, and notes. The Python app reads from GHL to resolve contact identity and writes back AI-derived insights, follow-up tasks, and campaign state — all gated behind shadow mode by default.

GHL is **not** the authoritative store for campaign processing state. Postgres is the process authority; GHL is the CRM view of that state.

---

## Auth contract

All GHL API requests use:

```
Authorization: Bearer {GHL_API_KEY}
Version: 2021-07-28
Content-Type: application/json
Accept: application/json
```

- `GHL_API_KEY` — per-location API key; never hard-coded; must come from `.env`
- `GHL_LOCATION_ID` — required for contact search queries; sent as a query param
- Base URL: `https://services.leadconnectorhq.com` (configurable via `GHL_BASE_URL`)

Read operations require `validate_for_ghl_reads()` (checks `ghl_api_key` and `ghl_location_id`).
Write operations require `validate_for_ghl_writes()` — only reachable when `ghl_writes_enabled=True`.

---

## Retry and error policy

Implemented in `GHLClient._request()` — `app/adapters/ghl.py`.

- Retryable status codes: `429`, `500`, `502`, `503`, `504`
- Retryable exception: `httpx.TimeoutException`
- Non-retryable: any 4xx except 429 — raises `GHLError` immediately
- Retry bound: `settings.ghl_retry_max` (default: 3 attempts)
- Backoff: exponential — `2^n` seconds per attempt (base: 1.0 s; pass `_retry_delay=0.0` in tests)
- After exhausting retries: raises `GHLError` with status code

All retries are logged at WARNING level with attempt count and path.

---

## Shadow gate contract

All write operations in `GHLClient` check `settings.ghl_writes_enabled` first.

### Settings flags

| Setting | Default | Effect |
|---|---|---|
| `GHL_WRITE_MODE` | `shadow` | `shadow` → writes are intercepted; `live` → writes execute |
| `GHL_WRITE_SHADOW_LOG_ONLY` | `true` | When in shadow mode, log-only (no side effects) |
| `GHL_WRITE_CONTACT_FIELDS` | `false` | Per-operation flag for field updates |
| `GHL_WRITE_NOTES` | `false` | Per-operation flag for note appends |
| `GHL_WRITE_TASKS` | `false` | Per-operation flag for task creation |
| `GHL_WRITE_SUMMARY` | `false` | Per-operation flag for student summary delivery |
| `GHL_WRITE_CAMPAIGN_STATE` | `false` | Per-operation flag for AI campaign field writes |
| `GHL_WRITE_FINALIZATION` | `false` | Per-operation flag for voicemail finalization writes |

`settings.ghl_writes_enabled` is computed from `ghl_write_mode == "live"`.

### Shadow write behavior

When `ghl_writes_enabled=False`:
1. `GHLClient._shadow_write()` is called instead of the real API
2. The payload is logged at INFO level with the operation name, `contact_id`, and payload keys
3. Note content is NOT logged (may contain transcript excerpts — privacy policy)
4. A structured shadow dict is returned: `{"shadow": True, "operation": ..., "contact_id": ..., "payload": ...}`
5. The real GHL API is never called

This shadow dict is treated as a successful result by callers — jobs complete normally in shadow mode.

Shadow mode is separate from `SHADOW_MODE_ENABLED` (which gates outbound calls/SMS/email at the job level). Both can be independently active.

---

## Read operations

### `search_contact_by_phone(phone: str) → dict | None`

- Endpoint: `GET /contacts/?locationId={id}&query={phone}`
- Returns first matching contact or None
- Phone number is redacted from logs (`phone=<redacted>`)
- Used during event routing to resolve `contact_id` from a call's phone number when GHL contact ID is not in the Synthflow payload

### `get_contact(contact_id: str) → dict`

- Endpoint: `GET /contacts/{contact_id}`
- Returns full contact record including `customFields` array
- Used before write operations to resolve custom field IDs at runtime
- Also used to read current AI campaign field value (`get_field_value()`)

---

## Field resolution

GHL custom fields do not have stable hard-coded IDs across locations. Field IDs are resolved at runtime from a fetched contact record.

### `resolve_field_id(field_label, contact) → str | None`

Matches on:
1. `field.name` (human-readable label)
2. `field.fieldKey` (snake_case system key)

Returns the field `id` used in write payloads. Returns None if not found.

### `get_field_value(field_label, contact) → str | None`

Same matching logic; returns the current `field.value` instead of the ID.

Used to read:
- `ai_campaign` — campaign name stored in GHL
- `ai_campaign_value` — voicemail tier (None / "0" / "1" / "2" / "3")

---

## Write operations

All write operations are implemented in `app/adapters/ghl.py` and are shadow-gated.

### `update_contact_fields(contact_id, field_updates: dict[str, str]) → dict`

- Endpoint: `PUT /contacts/{contact_id}`
- Payload: `{"customFields": [{"id": field_id, "value": value}, ...]}`
- Used for: student summary delivery, voicemail finalization fields (planned)
- Field labels in `field_updates` must be resolved to IDs via `resolve_field_id()` before live writes

### `create_task(contact_id, title, description="") → dict`

- Endpoint: `POST /contacts/{contact_id}/tasks`
- Payload: `{"title": ..., "status": "incompleted", "dueDate": null, ...}`
- `dueDate` is always null (`task_due_date_mode=blank`)
- No `assignedTo` — GHL-side assignment rules own final assignment
- Used for: follow-up task on completed call (via `create_crm_task` job)

### `append_note(contact_id, content) → dict`

- Endpoint: `POST /contacts/{contact_id}/notes`
- Payload: `{"body": content}`
- Content is NOT logged (transcript-level privacy)
- Currently wired to voicemail finalization (planned)

---

## Job contexts and lifecycle

### `create_crm_task` — `app/worker/jobs/crm_jobs.py`

Triggered after: completed call (call-through path), after AI analysis completes.
Queue: `callbacks`

**Flow:**
1. Claim job
2. Load `CallEvent` by `call_event_id`
3. **Dedupe check**: if `task_events` has a row with `call_event_id` + `status='created'`, skip
4. Call `GHLClient.create_task(contact_id, title="Completed call — {call_id}")`
5. Write `TaskEvent` row with `provider_task_id` (null in shadow mode)
6. Complete job

On error: creates `exceptions` row (type: `crm_task_failed`, severity: `warning`).

### `send_student_summary` — `app/worker/jobs/crm_jobs.py`

Triggered after: consent detection on completed call.
Queue: `callbacks`

**Flow:**
1. Claim job
2. Load `SummaryResult` by `call_event_id`
3. **Consent gate**: `summary_consent` must equal `"YES"` — any other value exits cleanly (no write, no error)
4. **Empty summary guard**: exits cleanly if `student_summary` is blank
5. Call `GHLClient.update_contact_fields(contact_id, {field_label: summary_text})`
   - Field label from `settings.ghl_field_student_summary` (default: `"Student Summary"`)
6. Write `audit_log` row (action: `student_summary_delivered`, operator_id: `system`)
7. Complete job

On error: creates `exceptions` row (type: `student_summary_delivery_failed`, severity: `warning`).

### Voicemail finalization (planned)

On terminal tier (`ai_campaign_value = "3"`), the voicemail job is planned to write:
- `LAST_CALL_STATUS` field
- `MARK_AS_LEAD` field
- `NOTES` field (transcript summary)

These writes require `GHL_WRITE_FINALIZATION=true`. Currently all fields resolve to None (not yet configured in `.env`).

---

## Unresolved external field IDs

The following GHL field identifiers are config-driven. Their values vary by GHL location and must be set in `.env` before live writes can execute:

| Env var | Purpose | Live write |
|---|---|---|
| `GHL_FIELD_AI_CAMPAIGN` | AI campaign field label | Planned |
| `GHL_FIELD_AI_CAMPAIGN_VALUE` | Voicemail tier field label | Planned |
| `GHL_FIELD_AI_LEAD_CLASSIFICATION` | AI classification field label | Planned |
| `GHL_FIELD_AI_LEAD_ASSIGN_TO` | Assignment field label | Planned |
| `GHL_FIELD_CALL_DETAILED_SUMMARY` | Call summary field label | Planned |
| `GHL_FIELD_STUDENT_SUMMARY` | Student recap field label | Active (student summary job) |
| `GHL_FIELD_VM_EMAIL_HTML` | Voicemail email HTML body field | Planned |
| `GHL_FIELD_VM_EMAIL_SUBJECT` | Voicemail email subject field | Planned |
| `GHL_FIELD_VM_SMS_TEXT` | Voicemail SMS text field | Planned |
| `GHL_FIELD_LAST_CALL_STATUS` | Last call status field | Planned |
| `GHL_FIELD_MARK_AS_LEAD` | Mark-as-lead flag field | Planned |
| `GHL_FIELD_NOTES` | Notes field (transcript) | Planned |
| `GHL_TASK_PIPELINE_ID` | GHL pipeline for task routing | Planned |
| `GHL_TASK_DEFAULT_OWNER_ID` | Default task assignee | Planned |

Field ID resolution happens at write time via `resolve_field_id()` from a freshly fetched contact — no field IDs are ever hard-coded.

---

## Idempotency and dedupe contracts

| Operation | Dedupe mechanism |
|---|---|
| `create_crm_task` | `task_events` table: one row per `call_event_id` with `status='created'` |
| `send_student_summary` | Consent gate (exact `summary_consent == 'YES'` check); job claims are atomic |
| All GHL writes | Shadow gate returns shadow dict without side effects — safe to replay |
| `append_note` | No dedupe — callers must avoid duplicate scheduling |

Job claims use `UPDATE WHERE version=expected; rowcount==0 → conflict` — concurrent claim attempts are rejected.

---

## Planned write sequences (not yet live)

### Campaign state write (voicemail tier progression)
When `GHL_WRITE_CAMPAIGN_STATE=true`:
1. Fetch contact (`get_contact`)
2. Resolve `ghl_field_ai_campaign_value` field ID
3. Write current tier value to contact field

### Voicemail finalization writes
When `GHL_WRITE_FINALIZATION=true` and tier reaches terminal (`ai_campaign_value = "3"`):
1. Write `LAST_CALL_STATUS` = final voicemail status
2. Write `MARK_AS_LEAD` = true/configured value
3. Append `NOTES` = AI summary of voicemail sequence

These are not yet configured or activated. Safe to leave `GHL_WRITE_FINALIZATION=false` until field IDs are confirmed.

---

## Error handling and observability

- `GHLError` is raised on non-retryable failures or retry exhaustion
- Errors in CRM jobs create `exceptions` rows (dashboard-visible, operator-retryable)
- All shadow writes are logged at INFO level
- Retry attempts are logged at WARNING level
- GHL auth failures (401/403) are non-retryable and raise `GHLError` immediately
- Alert trigger: GHL auth failure is classified as critical (see spec/10)

---

## Acceptance criteria

1. Given `GHL_WRITE_MODE=shadow`, when any write operation runs, then no GHL API call is made and a shadow dict is returned.
2. Given a completed call, when `create_crm_task` runs and `task_events` already has a `created` row for that `call_event_id`, then no second task is created.
3. Given `summary_consent != "YES"`, when `send_student_summary` runs, then it exits cleanly with no write and no error.
4. Given a transient GHL 429 response, when `_request()` runs, then it retries up to `ghl_retry_max` times with exponential backoff and raises `GHLError` after exhaustion.
5. Given a live write attempt (`ghl_writes_enabled=True`) with `ghl_api_key=None`, when `validate_for_ghl_writes()` is called, then a `ConfigError` is raised before any API call is made.
6. Given `GHL_WRITE_MODE=live` and valid credentials, when `create_task` is called, then `PUT /contacts/{id}/tasks` is called exactly once and a `task_events` row is written.
