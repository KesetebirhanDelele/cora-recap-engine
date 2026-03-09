# spec/07_api_contracts.md

## Public endpoints
- `POST /v1/webhooks/calls` — accept inbound or outbound call events.
- `POST /v1/exceptions/{id}/retry-now` — retry failed processing immediately.
- `POST /v1/exceptions/{id}/retry-delay` — retry after specified delay.
- `POST /v1/exceptions/{id}/cancel-future-jobs` — cancel scheduled jobs for the entity.
- `POST /v1/exceptions/{id}/force-finalize` — force a workflow to terminal state.

## Internal interfaces
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

