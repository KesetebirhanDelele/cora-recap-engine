# spec/15_end_to_end_test_call_plan.md

## Purpose
Define a practical end-to-end test plan and supporting script requirements so an operator can run a real outbound test and have the Synthflow agent call their phone number to validate the full engine.

This plan is intended to validate:
- app API availability
- webhook -> queue -> worker wiring
- Synthflow call launch integration
- completed-call event ingestion
- voicemail / call-through routing
- logging, audit, and reporting flow

---

## Test objective
A human operator shall be able to run one script or one command that:
1. submits a test call request to the application
2. causes the app to enqueue the correct worker job
3. triggers the configured Synthflow Make Call workflow
4. causes the Synthflow agent to call the operator’s phone
5. captures the resulting completed-call payload
6. stores the event in the application database
7. makes the result visible in logs, audit records, and dashboard/reporting paths

---

## Scope
### In scope
- single-recipient outbound end-to-end test call
- New Lead test path first
- manual confirmation of call receipt by the operator
- confirmation that the worker processes the result end to end

### Out of scope for first test iteration
- high-volume load testing
- multi-recipient campaigns
- real GHL write enablement unless separately approved
- automated voice-content quality scoring

---

## Required preconditions
Before the test script is used, the following must be true:
- API service is running
- worker service is running
- Redis/RQ is connected
- Postgres is available and migrations are applied
- Synthflow credentials are valid
- Synthflow Make Call workflow URL is configured
- Synthflow completed-call callback route is reachable from Synthflow
- GHL mode is set to read-only or shadow if real writes are not yet approved

---

## Proposed test script
### Suggested file
`execution/test_scripts/run_test_call.py`

### Purpose
Launch one test outbound call through the same path production will use.

### Required inputs
The script shall accept at minimum:
- `--phone`
- `--lead-name`
- `--campaign-name` (default `New_Lead`)
- `--dry-run` optional flag
- `--notes` optional metadata

### Example invocation
```bash
python execution/test_scripts/run_test_call.py --phone +17865551234 --lead-name "Test User" --campaign-name New_Lead
```

### Required script behavior
The script shall:
1. validate phone input format
2. build a test call request payload
3. submit the payload to the app’s internal/public call-launch path
4. print the queued job id or request id
5. poll or report how to observe worker/job progress
6. print correlation identifiers such as:
   - lead phone number
   - request id
   - call id when available

### Optional behavior
- wait for terminal completion with timeout
- print normalized outcome summary at the end
- fetch the completed event from the DB or API and display summarized fields

---

## Recommended test payload
The test request should minimally include:
- target phone number
- lead name
- campaign type/name
- source = test harness
- correlation id
- write mode context (read-only/shadow if applicable)

Recommended example structure:
```json
{
  "phone_number": "+17865551234",
  "lead_name": "Test User",
  "campaign_name": "New_Lead",
  "source": "e2e_test_harness",
  "notes": "manual end-to-end validation"
}
```

---

## System path under test
### Stage 1: launch request
The script sends a call-launch request to the application.

### Stage 2: queue handoff
The API records the request and enqueues the appropriate worker job.

### Stage 3: Synthflow launch
The worker calls the configured Synthflow Make Call workflow.

### Stage 4: real phone call
The operator’s phone rings from the Synthflow agent.

### Stage 5: completed-call callback
After the call ends, Synthflow sends completed-call payload to the application.

### Stage 6: terminal processing
The worker normalizes the outcome and stores the result.

---

## What the operator should test manually
### Test A: voicemail outcome
Do not answer the call or route to voicemail.

Expected outcome:
- completed-call payload received
- `call_status` equivalent to voicemail path
- event stored
- voicemail tier engine route triggered
- no duplicate jobs

### Test B: live answer outcome
Answer the call and allow a brief conversation.

Expected outcome:
- completed-call payload received
- transcript stored
- call-through path triggered
- summary/consent pipeline runs if enabled for the scenario

### Test C: early hangup / failure outcome
Answer and hang up quickly, or produce a deliberately short invalid interaction.

Expected outcome:
- completed-call event still stored if provider emits one
- normalized failure or short-call route is visible
- no duplicate retries for terminal event unless policy requires it

---

## Acceptance criteria
1. Given the test script is run with a valid phone number, when the script submits the request, then the app returns a request id or job id.
2. Given the worker is running, when the request is accepted, then the job is queued and picked up by the worker.
3. Given Synthflow configuration is valid, when the worker launches the call, then the operator receives the call from the configured voice agent.
4. Given the operator completes or ignores the call, when Synthflow emits the completed-call payload, then the app stores the event and normalizes the outcome.
5. Given the same test is replayed accidentally, when duplicate completion data arrives, then duplicate irreversible actions are prevented.
6. Given the system is in read-only or shadow mode for GHL, when the test call completes, then no unintended GHL mutation occurs.

---

## Observability requirements for the test
The system shall make the following visible during the test:
- request id / correlation id
- queued job id
- worker log lines showing job start and completion
- normalized outcome
- stored `call_id`
- any exception generated

Recommended locations:
- terminal/log output
- database rows
- dashboard exception/status views

---

## Recommended implementation additions
### 1. Add a dedicated API route for test call launch
Suggested route:
- `POST /v1/test/calls/outbound`

This route may be disabled outside dev/staging.

### 2. Add a helper to print live status
Suggested helper:
- `execution/test_scripts/watch_test_call.py`

This helper can watch DB or API state until completion.

### 3. Add DB correlation storage
The app should store enough metadata to correlate:
- test request
- worker job
- Synthflow completed call

---

## Example test workflow for the operator
1. Start API.
2. Start worker.
3. Confirm Redis and Postgres are healthy.
4. Run:
   ```bash
   python execution/test_scripts/run_test_call.py --phone +17865551234 --lead-name "Test User" --campaign-name New_Lead
   ```
5. Watch logs for queue/job ids.
6. Answer or ignore the phone call depending on the scenario being tested.
7. Confirm the completed-call payload is stored.
8. Confirm the normalized result matches expectations.

---

## Risks and mitigations
### Risk: test call accidentally mutates GHL
Mitigation: run in read-only or shadow write mode until approved.

### Risk: completed callback is not reachable
Mitigation: validate public callback route first and add a callback smoke test.

### Risk: operator cannot correlate test request to completed event
Mitigation: emit explicit correlation ids and print them in the test script.

### Risk: duplicate tests create duplicate downstream actions
Mitigation: reuse idempotency logic and dedupe by `call_id` plus action type.

---

## Implementation status — COMPLETE (2026-03-11)

All follow-up tasks completed:

| Task | Status | Location |
|---|---|---|
| Implement `run_test_call.py` | ✅ Done | `execution/test_scripts/run_test_call.py` |
| Add `watch_test_call.py` helper | ✅ Done | `execution/test_scripts/watch_test_call.py` |
| Add `POST /v1/test/calls/outbound` (dev/staging only) | ✅ Done | `app/api/routes/test_calls.py` — disabled in production via `APP_ENV` check |
| Test-call correlation logging | ✅ Done | `correlation_id` in job payload, propagated through all log lines |
| README documentation | ✅ Done | README § End-to-End Test Call with both PowerShell and script examples |
| Correlation storage in DB | ✅ Done | `correlation_id` stored in `scheduled_jobs.payload_json`; `call_id` in `call_events` |

## Follow-up tasks for Claude
1. ~~Implement `run_test_call.py`~~ — DONE
2. ~~Add a safe dev/staging-only outbound test route if needed~~ — DONE (`POST /v1/test/calls/outbound`)
3. ~~Add test-call correlation logging~~ — DONE
4. ~~Add documentation in README or runbook for live test-call execution~~ — DONE
5. Add one automated integration test around the non-telephony parts of the flow — pending

