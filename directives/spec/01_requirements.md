# spec/01_requirements.md

## Functional requirements

### Event ingestion and enrichment
- Accept call events with `call_id`, phones, status, start time, and duration.
- Enrich each event from GHL/LeadConnector and the canonical call-analysis source before routing.
- Normalize phones to E.164 and preserve raw values for audit.
- Store timestamps in UTC and render user-facing times in account timezone, defaulting to America/Chicago.
- Reject events with no resolvable identity and create an exception.

### Authoritative state and idempotency
- Use Postgres as the authoritative store for campaign state, call events, audit records, exceptions, and scheduled-job metadata.
- Use Redis + RQ for job execution, retries, and delayed processing while treating Postgres as canonical state.
- Treat GHL as authoritative for contacts, tasks, notes, and custom-field writes.
- Prevent duplicate irreversible actions by checking dedupe keys based on `(call_id, action_type)`.
- Support active Google Sheets shadow mode and mirror sheet data into Postgres while production logic remains database-authoritative.

### Routing and lifecycle management
- Route events into pending, call-through, or voicemail branches.
- Retry `queue` and `in-progress` statuses with bounded policy.
- Treat `hangup_on_voicemail` as voicemail input for applicable workflows.
- Send completed non-voicemail calls to call-through analysis.
- Surface invalid-route outcomes as exceptions.

### Task creation and CRM updates
- Create a GHL task for every completed non-voicemail call.
- Leave the task due date empty.
- Allow GHL-side assignment rules to own final task assignment.
- Update configured GHL contact fields with structured AI outputs and recap metadata.
- Support controlled note append or replace behavior for recap content.

### Student summary and consent
- Generate a student-facing summary for completed non-voicemail calls.
- Return blank summary output when transcript content is blank or not useful.
- Detect whether a summary was offered and whether consent was given using transcript-only evidence.
- Write the student summary back to GHL only when consent is `YES`.
- Do not write student summary content to GHL when consent is `NO`.
- Stamp summary outputs with model and prompt version metadata.

### Unified voicemail tier engine
- Support a canonical voicemail tier state model of `None -> 0 -> 1 -> 2 -> 3` for all outbound campaigns.
- Store `campaign_name` and `campaign_value` independently so behavior is policy-driven by campaign type.
- Cold Lead policy applies delays of 2 hours for None→0, 2 days for 0→1, 2 days for 1→2, and finalization without Synthflow callback for 2→3.
- New Lead policy uses the same tier numbering/state model while allowing different delay durations, actions, and finalization writes via configuration.
- Tier 3 is the terminal state for all campaigns.
- Finalization sets `AI Campaign = No` in GHL when defined by campaign policy.
- Voicemail content generation populates CRM fields used by GHL automations rather than sending SMS directly from the app.

### Failure handling and dashboard visibility
- Retry transient failures such as timeouts, HTTP 429, and 5xx responses with bounded backoff.
- Record-and-continue for non-blocking writes when safe.
- Halt and surface the exception in the dashboard when critical identity or required campaign state cannot be resolved.
- Display exceptions, retry status, and operator controls in the dashboard.
- Support Retry Now, Retry with Delay, Mark Resolved/Ignored with reason code, Cancel Future Jobs for lead/call/tier, and Force Finalize.
- Preserve scheduled jobs across restarts by storing canonical job state durably in Postgres.

## Non-functional requirements
- 99% of events processed within 2 minutes of event receipt, excluding intentional delays.
- Duplicate `call_id` write rate remains effectively zero under normal operation.
- Accepted jobs and scheduled retries survive worker restarts.
- Sensitive education/customer data at rest is encrypted.
- Access to transcripts, recordings/URLs, and CRM notes is role-restricted.
- Standard logs redact direct contact identifiers and transcript content.
- Retry caps, tier policy, and routing rules are configuration-driven.
- Stored event fixtures support replay for regression testing.
- Prompt families are versioned, source-controlled, shadow-testable, and rollback-capable.

### Reporting and analytics
- The system shall use Postgres-derived reporting tables or views as the authoritative source for dashboard metrics.
- Google Sheets may be mirrored for reconciliation during shadow mode but shall not be used as the authoritative reporting source.
- The dashboard shall support KPI reporting for at least:
    - Unique Contacts
    - Booked Appts
    - Calls Per Day
    - Call Completion Rate
    - Call Duration in Sec.
    - Pickup Rate
    - Voicemail Rate
    - Failed Rate

- The dashboard shall support visual filtering by at least:
    - date range
    - call type
    - campaign, where applicable
    - direction, where applicable

- The dashboard shall support cross-filtering between supported visuals so that a selection in one visual updates the others within the active filter context.

- Drill-down interactions are out of scope for the current phase.

- KPI tooltips are out of scope for the current phase.