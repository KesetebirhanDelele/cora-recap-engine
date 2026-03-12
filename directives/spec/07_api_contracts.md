# spec/07_api_contracts.md

## Public endpoints
- `POST /v1/webhooks/calls` — accept Synthflow completed-call events. Payload is normalised (Call_id → call_id, duration → duration_seconds, etc.) before processing.
- `GET  /v1/exceptions` — list open/failed exceptions (filter by status, severity, search).
- `GET  /v1/exceptions/{id}` — exception detail with audit trail.
- `POST /v1/exceptions/{id}/retry-now` — retry failed processing immediately.
- `POST /v1/exceptions/{id}/retry-delay` — retry after specified delay (`{"delay_minutes": N}`).
- `POST /v1/exceptions/{id}/cancel-future-jobs` — cancel all pending jobs for entity.
- `POST /v1/exceptions/{id}/force-finalize` — advance to terminal state, resolve exception.

## Dev/staging-only endpoints
The following route is registered only when `APP_ENV != production`:

- `POST /v1/test/calls/outbound` — trigger a real Synthflow outbound test call through the production code path. Returns `correlation_id` and `job_id`. GHL writes remain shadow-gated. Results arrive via `POST /v1/webhooks/calls` after the call ends.

  Request body:
  ```json
  {
    "phone_number": "+17865551234",
    "lead_name": "Test User",
    "campaign_name": "New_Lead",
    "source": "e2e_test_harness",
    "notes": ""
  }
  ```

## Synthflow webhook payload normalisation
Synthflow completed-call payloads use non-standard field names. The webhook handler normalises the following before storing or routing:

| Synthflow field | Internal field |
|---|---|
| `Call_id` / `callId` | `call_id` |
| `duration` | `duration_seconds` |
| `call_status` | primary routing field (also checks `Status`, `status`, `state`, `event`) |

Raw original payload is always preserved in `scheduled_jobs.payload_json` for audit.

## Internal interfaces
- `normalize_synthflow_outcome(payload)` — extract canonical routing status from completed-call payload; handles field aliases and defaults missing status to `"completed"` with a warning
- `launch_new_lead_call(phone, lead_name, campaign_name, metadata)` — trigger Synthflow Make Call workflow via `SYNTHFLOW_LAUNCH_WORKFLOW_URL`
- `get_call_details(call_id)`
- `upsert_contact_and_fields(contact_payload)`
- `create_ghl_task(task_payload)`
- `generate_summary(transcript_payload)`
- `detect_summary_consent(transcript_payload)`
- `generate_voicemail_content(vm_payload)`
- `schedule_synthflow_callback(callback_payload)`
- `mirror_google_sheet_rows(sync_payload)`
- `apply_campaign_policy(campaign_name, campaign_value, context)`

## Standard error
```json
{
  "error": {
    "code": "temporary_upstream_failure",
    "message": "dependency request failed",
    "retryable": true
  }
}
```

