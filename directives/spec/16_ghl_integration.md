# spec/16_ghl_integration.md

## Implementation status

| Area | Status |
|---|---|
| Auth (Bearer JWT + Version header) | IMPLEMENTED |
| Retry policy (429/5xx + timeout, bounded exponential backoff) | IMPLEMENTED |
| Shadow gate (`GHL_WRITE_MODE`, `ghl_writes_enabled`) | IMPLEMENTED |
| Read ops: `search_contact_by_phone`, `get_contact` | IMPLEMENTED (always active, not shadow-gated) |
| Field resolution: `resolve_field_id`, `get_field_value` | IMPLEMENTED |
| Write op: `create_task` with `assigned_to` + `due_date` | IMPLEMENTED (shadow-gated) |
| Write op: `update_contact_fields` (student summary delivery) | IMPLEMENTED (shadow-gated) |
| Write op: `update_contact_fields` (Path 1 — call analysis fields) | IMPLEMENTED (shadow-gated) |
| Write op: `update_contact_fields` (Path 2 — VM message fields) | IMPLEMENTED (shadow-gated) |
| Write op: `update_contact_fields` (Path 3 — finalization) | IMPLEMENTED (shadow-gated) |
| Write op: `append_note` | IMPLEMENTED (shadow-gated) |
| CRM task dedupe via `task_events` | IMPLEMENTED |
| Student summary consent gate | IMPLEMENTED |
| Audit log on summary delivery | IMPLEMENTED |
| GHL call analysis AI (`generate_ghl_call_analysis`) | IMPLEMENTED |
| `_persist_ghl_analysis` → `classification_results` | IMPLEMENTED |
| `LAST_CALL_STATUS` field write | PLANNED — not yet live |
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

Read operations require `validate_for_ghl_reads()` (checks `ghl_api_key` and `ghl_location_id`). **Reads are always active regardless of write-mode settings.**
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

### `create_task(contact_id, title, description="", assigned_to="", due_date="") → dict`

- Endpoint: `POST /contacts/{contact_id}/tasks`
- `assignedTo` and `dueDate` are omitted entirely from the payload when blank (not set to null) — prevents GHL from assigning to no-one
- `assigned_to` is populated from `GhlCallAnalysisResult.assign_to` (one of three staff GHL user IDs)
- `due_date` is populated from `GhlCallAnalysisResult.task_due_date` (ISO 8601 string derived from call transcript, e.g. "follow up Thursday at 2pm")
- Used for: follow-up task on completed call (via `create_crm_task` job)

### `append_note(contact_id, content) → dict`

- Endpoint: `POST /contacts/{contact_id}/notes`
- Payload: `{"body": content}`
- Content is NOT logged (transcript-level privacy)
- Currently wired to voicemail finalization (planned)

---

## GHL write paths

### Path 1 — Completed call AI analysis (`create_crm_task`)

Triggered after: completed call (call-through path), after AI analysis completes.
Queue: `callbacks`
File: `app/worker/jobs/crm_jobs.py`

**Flow:**
1. Claim job
2. Load `CallEvent` by `call_event_id`
3. **Dedupe check**: if `task_events` has a row with `call_event_id` + `status='created'`, skip
4. **GHL read**: `GHLClient.get_contact(contact_id)` — fetch live contact to resolve field IDs and get current tags
5. **AI analysis**: `generate_ghl_call_analysis(transcript, call_start_time_ms, duration_seconds, contact_phone, settings)` → `GhlCallAnalysisResult`
   - Model: `settings.openai_model_ghl_analysis` (default: `gpt-4o-mini`)
   - On error: fallback result with `create_task=False`, `lead_classification='not_a_lead'`
6. **GHL contact field writes** (shadow-gated): 5 fields —
   - `Mark as Lead` = `"Yes"`
   - `AI Lead Assign To` = `analysis.assign_to` (GHL user ID)
   - `Support Issue Ticket #3` = task/call description
   - `AI Lead Classification` = classification tag
   - `AI Campaign` = `"Yes"`
7. **GHL task creation** (shadow-gated, conditional on `analysis.create_task=True`): `GHLClient.create_task(contact_id, analysis.task_title, analysis.task_description, analysis.assign_to, analysis.task_due_date)`
8. Write `TaskEvent` row with `provider_task_id` (null in shadow mode)
9. **Persist analysis** → `_persist_ghl_analysis()` writes to `classification_results` with `prompt_family='ghl_call_analysis'`; fields: `lead_classification`, `is_lead`, `ai_campaign`, `call_detailed_summary`, `task_title`, `assign_to`, `call_start_time`, `task_due_date`
10. Complete job

On error: creates `exceptions` row (type: `crm_task_failed`, severity: `warning`).

### Path 2 — Voicemail-tier SMS/Email follow-up (`update_ghl_after_vm_message`)

Triggered after: each SMS or email sent during voicemail tier progression.
Queue: `callbacks`
File: `app/worker/jobs/crm_jobs.py`

Scheduled by `_schedule_ghl_vm_update()` in `channel_jobs.py` after every successful `send_sms_job` / `send_email_job`.

**Flow:**
1. Claim job
2. **GHL read**: `GHLClient.get_contact(contact_id)` — fetch live contact
3. **GHL contact field writes** (shadow-gated): 4–5 fields —
   - `Mark as Lead` = `"Yes"`
   - `Support Issue Ticket #2` = brief message identifier (email subject or SMS snippet, ≤ 50 chars)
   - `Message` = full generated SMS/email body
   - `AI Campaign` = `"Yes"`
   - `Support Issue Ticket #4` = latest lead classification from `classification_results` (via `_get_latest_classification()`)
4. Complete job

On error: logged as non-fatal warning; does not create exception row (voicemail messaging already succeeded).

### Path 3 — Campaign finalization (`_finalize_campaign`)

Triggered when: voicemail tier reaches terminal (`tier >= 2` with `finalize=True`).
File: `app/worker/jobs/voicemail_jobs.py`

**Flow:**
1. **GHL contact field writes** (shadow-gated): 2 fields —
   - `Mark as Lead` = `"Yes"`
   - `AI Campaign` = `"No"`
2. These writes signal GHL automations to stop AI-driven outreach for this lead.

Requires `GHL_WRITE_FINALIZATION=true`. Currently runs in shadow mode.

---

## `GhlCallAnalysisResult` — AI output contract

Produced by `generate_ghl_call_analysis()` in `app/core/ai_message_generator.py`.
Prompt family: `ghl_call_analysis` / version `v1` — `app/prompts/families/ghl_call_analysis.py`.

| Field | Type | Description |
|---|---|---|
| `task_title` | str | Short task title for GHL (e.g. "Follow up: interested, wants to talk Thursday") |
| `task_description` | str | Full call narrative for the task body |
| `assign_to` | str | GHL user ID (Bala=`yIhCTptvoNLixaWkLcRd`, Shveta=`mW2OSEYWWGDSB9JcKBcr`, Taiwo=`93bhNRgb5pzSoHmaSimH`) |
| `is_lead_classification` | bool | Whether AI classified this as a likely lead |
| `lead_classification` | str | Tag value for AI Lead Classification field |
| `create_task` | bool | Whether to create a GHL task (False for do_not_call, enrolled, etc.) |
| `outbound_call_details` | str | Brief structured call detail for notes |
| `call_detailed_summary` | str | Full narrative call summary (stored in `classification_results`, shown in dashboard timeline) |
| `ai_campaign` | str | "Yes" / "No" for AI Campaign field |
| `call_start_time_formatted` | str | Human-readable call start time string |
| `task_due_date` | str | ISO 8601 due date extracted from transcript ("follow up Thursday at 2pm" → next occurrence) |

---

## Job contexts and lifecycle

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

---

## Field mapping reference

All GHL field identifiers are config-driven. Field IDs are resolved at runtime via `resolve_field_id()` from a freshly fetched contact — no IDs are ever hard-coded.

| Env var | Default value | Used in | Status |
|---|---|---|---|
| `GHL_FIELD_MARK_AS_LEAD` | `"Mark as Lead"` | Path 1, 2, 3 | Active (shadow-gated) |
| `GHL_FIELD_AI_LEAD_ASSIGN_TO` | `"AI Lead Assign To"` | Path 1 | Active (shadow-gated) |
| `GHL_FIELD_SUPPORT_TICKET_3` | `"Support Issue Ticket #3"` | Path 1 — task description | Active (shadow-gated) |
| `GHL_FIELD_AI_LEAD_CLASSIFICATION` | `"AI Lead Classification"` | Path 1 | Active (shadow-gated) |
| `GHL_FIELD_AI_CAMPAIGN` | `"Yes"` (value) | Path 1, 2, 3 | Active (shadow-gated) |
| `GHL_FIELD_SUPPORT_TICKET_2` | `"Support Issue Ticket #2"` | Path 2 — brief identifier | Active (shadow-gated) |
| `GHL_FIELD_MESSAGE` | `"Message"` | Path 2 — full message body | Active (shadow-gated) |
| `GHL_FIELD_SUPPORT_TICKET_4` | `"Support Issue Ticket #4"` | Path 2 — classification tag | Active (shadow-gated) |
| `GHL_FIELD_STUDENT_SUMMARY` | `"Student Summary"` | Student summary job | Active (shadow-gated) |
| `GHL_FIELD_VM_EMAIL_HTML` | `"Support Issue Ticket #2"` | Legacy VM email field | Config-driven |
| `GHL_FIELD_VM_EMAIL_SUBJECT` | `"Message:"` | Legacy VM email subject | Config-driven |
| `GHL_FIELD_VM_SMS_TEXT` | `"Support Issue Ticket #4"` | Legacy VM SMS field | Config-driven |
| `GHL_FIELD_LAST_CALL_STATUS` | (unset) | Last call status | Planned |
| `GHL_FIELD_NOTES` | (unset) | Notes (transcript) | Planned |
| `GHL_TASK_PIPELINE_ID` | (unset) | Task pipeline routing | Planned |
| `GHL_TASK_DEFAULT_OWNER_ID` | (unset) | Default task assignee | Planned |

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

### Last call status and notes
When `GHL_WRITE_FINALIZATION=true` (fully activated):
1. Write `LAST_CALL_STATUS` = final voicemail status
2. Append `NOTES` = AI summary of voicemail sequence

These are not yet configured or activated. Safe to leave `GHL_WRITE_FINALIZATION=false` until field IDs are confirmed.

Note: `MARK_AS_LEAD` and `AI_CAMPAIGN` writes in Path 3 are already implemented via `_finalize_campaign()` — these are the live parts of finalization that run in shadow mode today.

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
7. Given a completed call, when `create_crm_task` runs, then `_persist_ghl_analysis()` writes a `classification_results` row with `prompt_family='ghl_call_analysis'` containing `call_detailed_summary` and `lead_classification`.
8. Given an SMS or email successfully sent, when `_schedule_ghl_vm_update()` runs, then a `update_ghl_after_vm_message` job is enqueued with the message body.
9. Given a GHL read (`get_contact`) called during shadow-mode write path, then the read executes normally and the result is used to resolve field IDs even when writes are shadow-gated.
10. Given a voicemail tier reaching terminal, when `_finalize_campaign()` runs, then `Mark as Lead=Yes` and `AI Campaign=No` are written (shadow-gated) to the GHL contact.
