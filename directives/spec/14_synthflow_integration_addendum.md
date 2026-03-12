# spec/14_synthflow_integration_addendum.md

## Implementation status — COMPLETE (2026-03-11)

All requirements in this spec are implemented:

| Requirement | Implementation |
|---|---|
| `launch_synthflow_call()` | `SynthflowClient.launch_new_lead_call()` — `app/adapters/synthflow.py` |
| `normalize_synthflow_outcome()` | `app/worker/jobs/call_processing.py` — handles `call_status`/`Status`/`status`/`state`/`event` aliases; defaults to `completed` with warning if missing |
| `ingest_synthflow_completed_call()` | `process_call_event()` + `_create_call_event()` — `app/worker/jobs/call_processing.py` |
| Dedupe by `call_id` | `dedupe_key = "{call_id}:process_call_event"` unique constraint in `call_events` |
| Persist raw payload | `raw_payload_json` column in `call_events` |
| Persist normalised fields | `model_id`, `lead_name`, `agent_phone_number`, `timeline`, `telephony_*` — migration `0004_call_event_synthflow_fields` |
| Voicemail routing | `_VOICEMAIL_STATUSES = {"voicemail", "hangup_on_voicemail"}` → `_route_to_voicemail()` |
| Call-through routing | `_COMPLETED_STATUSES = {"completed"}` → `_route_to_call_through()` → `classify_call_event` |
| `executed_actions` logging | `_log_executed_actions()` — logs failures ≥ 400 as warnings |
| Webhook field normalisation | `webhooks.py` normalises `Call_id` → `call_id`, `duration` → `duration_seconds` |
| `SYNTHFLOW_LAUNCH_WORKFLOW_URL` config | `settings.synthflow_launch_workflow_url` with `validate_for_synthflow_launch()` |
| Make Call worker job | `launch_outbound_call_job` — `app/worker/jobs/outbound_jobs.py` |

---

## Purpose
Define the Synthflow integration contract for outbound New Lead calling based on the observed live workflows:
- **Cora Outbound NewLeads - Make Call**
- **Cora Outbound NewLeads - Call Completed**

This document clarifies how Synthflow is used in the system, what payloads matter, how the same voice agent participates in both workflows, and what the Python app must treat as authoritative.

---

## Integration overview
Synthflow is used in two distinct but connected roles for the New Lead campaign:

1. **Call initiation**
   - The app triggers a Synthflow workflow that starts an outbound call using the New Lead agent.

2. **Call completion reporting**
   - After the call ends, Synthflow produces a completed-call payload containing the call outcome, transcript, timeline, recording URL, and telephony facts.

These two workflows use the **same assistant**:
- `Cora - Outbound Admissions Agent - New L`

The system must treat them as two phases of one outbound-call lifecycle.

---

## Workflow A: Make Call
### Name
`Cora Outbound NewLeads - Make Call`

### Role
Starts an outbound call using the configured New Lead assistant.

### Observed structure
- Trigger: `Catch Webhook`
- Action: `Make Phone Call and Get Call Data`
- Connection: `Cora OB New Leads`
- Assistant: `Cora - Outbound Admissions Agent - New L`

### Contract interpretation
The Python app shall treat this workflow as a **call-launch endpoint**.

The app is responsible for:
- invoking the workflow webhook
- passing the required call-launch input payload
- recording the launch attempt in authoritative state
- treating launch success as **call requested**, not **call completed**

### Important note
Observed sample output from the workflow trigger was only the `Catch Webhook` metadata, not the completed business output of the phone call.

Therefore, the app shall **not** treat the output of this workflow trigger sample as call result data.

### Required app behavior
When the app triggers this workflow it shall:
- create or update a scheduled/initiated call record
- log the call-launch request payload
- record the intended campaign policy context
- wait for the completed-call workflow payload before making terminal workflow decisions

---

## Workflow B: Call Completed
### Name
`Cora Outbound NewLeads - Call Completed`

### Role
Emits the final call result for calls handled by the New Lead assistant.

### Observed structure
- Trigger/event: completed call
- Connection: `Cora OB New Leads`
- Assistant: `Cora - Outbound Admissions Agent - New L`

### Authoritative output fields observed
The completed-call payload includes at minimum:
- `call_id`
- `model_id`
- `duration`
- `end_call_reason`
- `lead_phone_number`
- `timeline`
- `executed_actions`
- `recording_url`
- `transcript`
- `call_status`
- `start_time`
- `lead_name`
- `agent_phone_number`
- `telephony_duration`
- `telephony_start`
- `telephony_end`
- `campaign_type`

### Contract interpretation
The Python app shall treat this workflow payload as the **authoritative completed-call event source** for Synthflow-driven outbound calls.

### Required app behavior
When this payload is received, the app shall:
- dedupe by `call_id`
- normalize the outcome into internal routing categories
- persist the raw payload for audit
- persist normalized fields used for routing and reporting
- route into:
  - call-through path, or
  - voicemail/tier path, or
  - failure/exception path

---

## Shared assistant model
Both workflows use the same assistant:
- `Cora - Outbound Admissions Agent - New L`

### System meaning
The assistant identity defines a shared campaign execution context.

The app shall treat:
- the **Make Call** workflow as the call-launch interface for that assistant
- the **Call Completed** workflow as the result interface for that assistant

### Consequence
The app must be able to correlate initiation attempts and completion events through fields such as:
- `call_id`
- `lead_phone_number`
- assistant/campaign policy context
- scheduled job metadata where applicable

---

## Routing-relevant fields
The following fields are critical for the Python engine.

### 1. `call_id`
Primary idempotency and event-correlation key for completed calls.

### 2. `call_status`
Observed example:
- `hangup_on_voicemail`

This field shall be used as a high-priority routing signal.

### 3. `end_call_reason`
Observed example:
- `voicemail`

This field shall be used with `call_status` to determine normalized outcome.

### 4. `transcript`
Used for:
- summary generation
- consent detection where applicable
- call-through analysis
- audit and reporting

### 5. `timeline`
Used for:
- richer analysis
- debugging conversation flow
- validating transcript-derived interpretations

### 6. `recording_url`
Used for:
- recording access metadata
- audit/troubleshooting
- optional manual QA

### 7. telephony timing fields
Used for:
- operational reporting
- duration reconciliation
- support/debugging

---

## Normalization rules
The app shall normalize Synthflow completion payloads into internal workflow states.

### Example mapping
- `call_status = hangup_on_voicemail` + `end_call_reason = voicemail`
  - route to voicemail/tier engine

- completed human conversation with usable transcript
  - route to call-through path

- technical failure or missing required identity
  - route to exception path

The app must persist both:
- raw provider values
- normalized internal route outcome

---

## Executed actions handling
Completed-call payloads may include `executed_actions` run inside the assistant.

### Observed example
A GHL-related custom action returned:
- `401`
- `Invalid JWT`

### Interpretation
The assistant may attempt in-call external lookups, but those actions are not guaranteed to succeed.

### Required app rule
The Python app shall **not assume** that in-agent external actions succeeded unless the payload explicitly shows successful results.

### Operational requirement
Any failed critical in-agent external action observed in completed-call payloads shall be:
- logged
- retained in audit data
- available for dashboard diagnostics if needed

### Architectural implication
The app should treat Synthflow’s completed-call payload as authoritative for call result facts, while treating in-agent external integrations as best-effort unless separately validated.

---

## Security and auth note
The Make Call and Call Completed integrations must be authenticated and bounded by app-controlled secrets.

The app shall support:
- configured Synthflow base URL
- configured Synthflow model/assistant context where required
- webhook verification or allowlist strategy where available
- retry-safe handling of duplicate completion events

---

## Data model impact
The operational data model shall support at least:
- initiated outbound call record
- completed call event record
- raw provider payload storage
- normalized route outcome
- provider-specific fields such as `call_status`, `end_call_reason`, `recording_url`, `telephony_*`

---

## Reporting impact
The reporting layer shall treat Synthflow completed-call payloads as the source for:
- voicemail rate
- failed rate
- completion rate
- duration metrics
- call volume by campaign/call type

---

## Acceptance criteria
1. Given the app launches a Synthflow New Lead call, when the Make Call workflow is invoked, then the app records the launch attempt without treating it as terminal call outcome.
2. Given Synthflow emits a completed-call payload, when the payload arrives, then the app dedupes by `call_id` and persists the raw payload.
3. Given a completed-call payload contains `call_status = hangup_on_voicemail`, when normalization runs, then the call routes into the voicemail tier engine.
4. Given a completed-call payload contains transcript, timeline, and recording URL, when the app stores the event, then those fields are available for downstream analysis and reporting.
5. Given `executed_actions` contains a failed in-agent external call, when the event is processed, then the failure is logged and does not silently masquerade as successful enrichment.

---

## Implementation guidance
Recommended app-level interfaces:
- `launch_synthflow_call(payload)`
- `ingest_synthflow_completed_call(payload)`
- `normalize_synthflow_outcome(payload)`

Recommended config additions if not already present:
- Synthflow launch workflow URL
- Synthflow completed-call webhook route/secret handling
- per-campaign assistant mapping

---

## Risks and mitigations
### Risk: initiation workflow output is mistaken for completed call output
Mitigation: treat Make Call workflow as request/launch only.

### Risk: duplicate completed-call events create duplicate actions
Mitigation: dedupe by `call_id` and action type.

### Risk: in-agent GHL action failures lead to false assumptions
Mitigation: explicitly inspect `executed_actions` and never assume success.

### Risk: provider-specific statuses drift from internal routing semantics
Mitigation: centralize normalization rules and persist both raw and normalized values.

