# spec/14_synthflow_integration_addendum.md

## Implementation status — COMPLETE (updated 2026-04-21)

All requirements in this spec are implemented:

| Requirement | Implementation |
|---|---|
| `launch_synthflow_call()` | `SynthflowClient.launch_new_lead_call()` — `app/adapters/synthflow.py` |
| `normalize_synthflow_outcome()` | `app/worker/jobs/call_processing.py` — handles `call_status`/`Status`/`status`/`state`/`event` aliases; defaults to `completed` with warning if missing |
| `ingest_synthflow_completed_call()` | `process_call_event()` + `_create_call_event()` — `app/worker/jobs/call_processing.py` |
| Dedupe by `call_id` | `dedupe_key = "{call_id}:process_call_event"` unique constraint in `call_events` |
| Persist raw payload | `raw_payload_json` column in `call_events` |
| Persist normalised fields | `model_id`, `lead_name`, `agent_phone_number`, `timeline`, `telephony_*` — migration `0004_call_event_synthflow_fields` |
| Voicemail routing | `VOICEMAIL_STATUSES = {"voicemail", "hangup_on_voicemail", "left_voicemail", "voicemail_detected", "machine_detected"}` → `_route_to_voicemail()` |
| Call-through routing | `_COMPLETED_STATUSES = {"completed"}` → `_route_to_call_through()` → `classify_call_event` |
| `executed_actions` logging | `_log_executed_actions()` — logs failures ≥ 400 as warnings |
| Webhook field normalisation | `normalize_synthflow_payload()` in `webhooks.py`: resolves `call_id` from `Call_id`/`callId`/`call_id`; maps `duration` → `duration_seconds`; derives `contact_id` from phone fields; infers `campaign_name` from `Agent` field |
| `Agent`-field campaign inference | `Agent` containing `coldlead`/`cold lead` → `"Cold Lead"`; `newlead`/`new lead` → `"New Lead"`; overrides payload's always-`"New Lead"` default |
| Per-campaign Make Call webhook URL | `settings.get_synthflow_launch_url(campaign_name)` — selects Cold Lead or New Lead URL; raises `ConfigError` if missing |
| Make Call worker job | `launch_outbound_call_job` — `app/worker/jobs/outbound_jobs.py` |

---

## Purpose
Define the Synthflow integration contract for outbound calling across all campaigns based on the live workflows:
- **Cora Outbound NewLeads - Make Call** (New Lead campaign)
- **Cora Outbound ColdLead - Make Call** (Cold Lead campaign)
- **Cora Outbound NewLeads - Call Completed** (completion webhook, both campaigns)

This document clarifies how Synthflow is used in the system, what payloads matter, how voice agents map to campaigns, and what the Python app must treat as authoritative.

---

## Voice Agents

Each campaign uses a dedicated Synthflow voice agent. The `model_id` in `call_events` identifies which agent handled the call.

| Campaign  | Voice Agent Name                          | model_id                               |
|-----------|-------------------------------------------|----------------------------------------|
| Cold Lead | Cora Outbound ColdLead Agent              | `95fd0659-7446-423c-bc51-764c3060c90f` |
| New Lead  | Cora - Outbound Admissions Agent - New L  | `2608601d-bce6-4bb8-bc0f-f7df9dbf5971` |
| Inbound   | Cora Inbound Agent                        | `f98454c1-2cd4-476c-b6f2-c5c425689e61` |

**Rule:** VM-tier retry calls must use the same voice agent as the lead's current campaign.
`launch_outbound_call_job` passes `campaign_name` to `launch_new_lead_call()`, which routes to the correct webhook — Synthflow selects the voice agent based on the workflow triggered.

---

## Make Call Webhook URLs (per campaign)

The app selects the webhook URL based on the lead's `campaign_name` at call time.
Routing logic: `settings.get_synthflow_launch_url(campaign_name)` in `app/config/settings.py`.

| Campaign  | Env var                              | Webhook ID              |
|-----------|--------------------------------------|-------------------------|
| New Lead  | `SYNTHFLOW_LAUNCH_WORKFLOW_URL_New`  | `p6ihFj7HmplXM2WiuVsaC` |
| Cold Lead | `SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold` | `33J546NiXxUUIRCbywNVH` |

**Do not use:** `JylDXjF8QB0Skr5cQzGGm` — this was a test/Nexus workflow that does not place calls. All historical calls that hit this URL were silently dropped by Synthflow.

Config health checks in `app/core/mode_flags.py` will flag missing URLs on the dashboard System Controls page.

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

### call_id resolution (priority order)
1. `call_id` (lowercase — direct match)
2. `Call_id` (Synthflow capitalisation variant)
3. `callId` (camelCase variant)
First truthy value wins. Empty strings are skipped.

### campaign_name resolution (priority order)
1. `Agent` field keyword match (overrides payload):
   - contains `coldlead` or `cold lead` → `"Cold Lead"`
   - contains `newlead` or `new lead` → `"New Lead"`
2. Payload `campaign_name` (if no Agent keyword matched)
3. Default `"New Lead"` (if both absent)

Rationale: Synthflow sends `campaign_name = "New Lead"` for all workflows regardless of which agent ran the call. The `Agent` field value (e.g. `"Cora Outbound ColdLead Completed Call"`) reliably identifies the campaign.

### voicemail status set
`VOICEMAIL_STATUSES = {"voicemail", "hangup_on_voicemail", "left_voicemail", "voicemail_detected", "machine_detected"}`
Any of these routes to the voicemail tier engine.

### Example mappings
- `call_status = hangup_on_voicemail` + `end_call_reason = voicemail` → voicemail/tier engine
- `call_status = completed` with usable transcript → call-through path
- Technical failure or missing identity → exception path

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

---

## Known operational constraint: HTTP step concurrency limit

**Observed 2026-04-27 (production incident).**

### What happens
Synthflow's HTTP step — the step inside the completed-call workflow that POSTs to `POST /v1/webhooks/calls` — has an undocumented concurrency limit. When a large number of calls complete simultaneously, the HTTP step drops webhooks silently. The call happened in Synthflow; Cora never receives the completion event. Affected leads are left with:
- `launch_outbound_call` job status = `completed` (call was launched successfully)
- No `call_event` row created (webhook never arrived)
- Voicemail tier not advanced
- No next call scheduled

### Root cause
All rescheduled calls deferred to the next active window (e.g. 9 AM CDT) were previously assigned `run_at = window_start` exactly, causing all of them to fire at the same second. On 2026-04-27, 366 calls fired simultaneously at 14:00:00 UTC, overwhelming the HTTP step.

### Fix implemented (2026-04-27, tightened 2026-04-29)
`_compute_window_run_at(session, window_start)` in `app/worker/jobs/outbound_jobs.py` assigns a slot-based `run_at` at reschedule time:
- Counts all pending `launch_outbound_call` jobs in the 4-hour window
- Divides by `_CALL_BATCH_SIZE = 4` to get the slot index
- Returns `window_start + slot * _CALL_SLOT_SECONDS` (300 s = 5 min per slot)

Effect: 4 calls per 5-minute slot. 100 deferred calls spread over ~2 hours. Prevents burst at window open.

Original values (2026-04-27): `_CALL_BATCH_SIZE = 10`, `_CALL_SLOT_SECONDS = 120`. Tightened to 4/300 on 2026-04-29 after April 28 incident confirmed 10/2-min still exceeded Synthflow's HTTP step capacity.

### Design rule
**Never assign `run_at = window_start` directly for rescheduled calls.** Always call `_compute_window_run_at(session, window_start)` so the slot-based spacing is applied.

### Known limitation: voicemail retry callbacks inherit burst pattern
`_compute_window_run_at()` is only called for calls rescheduled to the **next active window** (out-of-hours deferral). Voicemail retry calls scheduled by `_schedule_retry_outbound_call()` in `voicemail_jobs.py` use `now + delay_minutes` directly — no slot spreading is applied at schedule time.

Consequence: if a large burst of calls fires simultaneously, their 24-hour (New Lead) or 48-hour (Cold Lead) VM retry callbacks will all land in the same 5-minute window, recreating the burst at the next tier. The worker's active-window deferral path then applies slot spreading for out-of-hours calls, but intra-window bursts are not automatically spread.

**Mitigation when detected:** run the slot redistribution SQL (see runbook) to manually spread pending `launch_outbound_call` jobs before they fire:
```sql
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY run_at ASC, id ASC) - 1 AS rn
    FROM scheduled_jobs
    WHERE job_type = 'launch_outbound_call' AND status = 'pending' AND run_at >= NOW()
),
base AS (SELECT GREATEST(NOW(), MIN(run_at)) AS t FROM ranked)
UPDATE scheduled_jobs sj
SET run_at = b.t + (FLOOR(r.rn / 4) * INTERVAL '5 minutes')
FROM ranked r, base b
WHERE sj.id = r.id AND sj.run_at != b.t + (FLOOR(r.rn / 4) * INTERVAL '5 minutes');
```
Run as a SELECT first to preview changes. Only updates `pending` jobs.

### Detection query
```sql
-- Calls launched in a burst with no webhook return:
SELECT DATE_TRUNC('minute', sj.run_at) AT TIME ZONE 'America/Chicago' AS minute_cst,
       ls.ai_campaign_value AS vm_tier, COUNT(*) AS leads
FROM lead_state ls
JOIN LATERAL (
    SELECT run_at FROM scheduled_jobs
    WHERE entity_id = ls.contact_id
      AND job_type  = 'launch_outbound_call'
      AND status    = 'completed'
    ORDER BY run_at DESC LIMIT 1
) sj ON TRUE
WHERE ls.ai_campaign_value IN ('0','1','2')
  AND (ls.status IS NULL OR ls.status NOT IN ('closed','terminal'))
  AND ls.do_not_call IS NOT TRUE
  AND NOT EXISTS (SELECT 1 FROM scheduled_jobs WHERE entity_id = ls.contact_id
                    AND job_type = 'launch_outbound_call' AND status IN ('pending','claimed'))
  AND NOT EXISTS (SELECT 1 FROM call_events ce WHERE ce.contact_id = ls.contact_id
                    AND ce.created_at >= sj.run_at)
GROUP BY 1, 2 ORDER BY 1 DESC, 2;
```
A large spike at a single minute in the results confirms a burst event.

### Recovery procedures

**General-purpose recovery script (2026-04-28):**
`execution/recover_webhook_drop_20260428.py` handles both tier-advancement and AI-queuing for any burst incident. It loads a Synthflow CSV export, matches against DB contacts with missing webhooks, and routes each contact:
- **Path A** (call found in CSV — completed): creates call_event, queues `run_call_analysis`
- **Path B** (no CSV match — assumed hangup_on_voicemail): advances voicemail tier, schedules next call if not terminal

```bash
# Copy CSV to container first
docker compose exec worker-default mkdir -p /app/tmp
docker compose cp /path/to/calls.csv worker-default:/app/tmp/calls.csv

# Copy and run script
docker compose cp execution/recover_webhook_drop_20260428.py worker-default:/app/execution/recover_webhook_drop_20260428.py
docker compose exec worker-default python execution/recover_webhook_drop_20260428.py        # dry run
docker compose exec worker-default python execution/recover_webhook_drop_20260428.py --live # live
```

Key parameters in the script:
- `_INCIDENT_START` / `_INCIDENT_END`: UTC window of the burst (extend `_INCIDENT_END` if contacts from the next UTC day are affected)
- NOT EXISTS window: `INTERVAL '7 days'` — wide enough to avoid re-detecting contacts already recovered
- CSV column fallbacks: supports both `Duration (s)` and `Duration`, `Recording Link` and `Recording URL`

**Verification after recovery:**
```sql
-- Confirm no contacts remain stuck
SELECT COUNT(*) FROM scheduled_jobs sj
WHERE sj.job_type = 'launch_outbound_call' AND sj.status = 'completed'
  AND sj.updated_at >= :incident_start AND sj.updated_at <= :incident_end
  AND NOT EXISTS (
      SELECT 1 FROM call_events ce
      WHERE ce.contact_id = sj.payload_json->>'contact_id'
        AND ce.created_at >= sj.updated_at - INTERVAL '10 minutes'
        AND ce.created_at <= sj.updated_at + INTERVAL '7 days'
  );
```
Expected: 0 after successful recovery.

**Note:** A second class of stuck leads exists from intermittent Synthflow HTTP step failures unrelated to burst concurrency (individual webhook drops spread throughout the day). These leads appear with counts of 1–3 per minute across many time slots — not a single-minute spike. Recovery approach is the same script, but root cause is Synthflow reliability, not call volume.

