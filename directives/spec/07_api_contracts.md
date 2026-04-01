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

### Synthflow adapter — `app/adapters/synthflow.py`
- `SynthflowClient.launch_new_lead_call(phone, lead_name, campaign_name, metadata)` — triggers Make Call workflow via `SYNTHFLOW_LAUNCH_WORKFLOW_URL`
- `SynthflowClient.schedule_callback(phone, tier, delay_minutes)` — schedules a voicemail tier callback

### GHL adapter — `app/adapters/ghl.py`
See `spec/16_ghl_integration.md` for full GHL integration contract.
- `GHLClient.search_contact_by_phone(phone)` — resolve contact_id from phone
- `GHLClient.get_contact(contact_id)` — fetch full contact record
- `GHLClient.update_contact_fields(contact_id, field_updates)` — write custom fields (shadow-gated)
- `GHLClient.create_task(contact_id, title, description)` — create follow-up task (shadow-gated)
- `GHLClient.append_note(contact_id, content)` — append note to contact (shadow-gated)

### Call processing — `app/worker/jobs/call_processing.py`
- `normalize_synthflow_outcome(payload)` — extract canonical routing status; handles field aliases; defaults to `"completed"` with a warning if missing
- `process_call_event(job_id)` — main call event ingest: dedupe, persist, route to voicemail or call-through path

### AI services — `app/services/ai.py`
- `run_call_analysis(call_event_id)` — classify lead stage from transcript
- `generate_student_summary(call_event_id)` — generate recap and detect consent

### Campaign — `app/core/campaigns.py`
- `evaluate_campaign_switch(campaign_name, intent)` — pure function; returns new campaign name or None
- `apply_campaign_switch(session, lead, new_campaign_name, reason)` — updates lead, writes audit_log row

### Intent — `app/core/intent_detection.py`
- `detect_intent(transcript, executed_actions, duration_seconds)` — rule-based intent detection; returns intent string or None

### Intent handlers — `app/core/intent_actions.py`
- `handle_intent(session, lead, intent, call_event, settings)` — applies handler for detected intent; schedules downstream jobs

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

