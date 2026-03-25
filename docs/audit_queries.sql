-- =============================================================================
-- Cora Recap Engine — Audit SQL Queries
-- =============================================================================
-- Purpose: Operational queries for inspecting system state after E2E runs,
--          debugging production issues, and validating data integrity.
--
-- All queries target the Postgres schema used by the production system.
-- For SQLite (tests), JSON path syntax differs: use JSON_EXTRACT instead of ->>
-- =============================================================================


-- =============================================================================
-- SECTION 1: Lead State Inspection
-- =============================================================================

-- 1a. Full lead state for a specific contact
SELECT
    contact_id,
    campaign_name,
    ai_campaign_value,
    status,
    do_not_call,
    invalid,
    preferred_channel,
    next_action_at,
    last_replied_at,
    version,
    updated_at
FROM lead_state
WHERE contact_id = '<contact_id>';


-- 1b. All leads currently in nurture state
SELECT
    contact_id,
    campaign_name,
    ai_campaign_value,
    next_action_at,
    updated_at
FROM lead_state
WHERE status = 'nurture'
ORDER BY next_action_at ASC;


-- 1c. All enrolled leads (campaign terminated successfully)
SELECT
    contact_id,
    campaign_name,
    ai_campaign_value,
    updated_at
FROM lead_state
WHERE status = 'enrolled'
ORDER BY updated_at DESC;


-- 1d. Terminal leads (tier=3 — campaign exhausted without enrollment)
SELECT
    contact_id,
    campaign_name,
    status,
    ai_campaign_value,
    updated_at
FROM lead_state
WHERE ai_campaign_value = '3'
  AND status != 'enrolled'
ORDER BY updated_at DESC;


-- 1e. Do-not-call suppression list
SELECT
    contact_id,
    normalized_phone,
    status,
    updated_at
FROM lead_state
WHERE do_not_call = TRUE
ORDER BY updated_at DESC;


-- 1f. Leads by campaign distribution
SELECT
    campaign_name,
    status,
    COUNT(*) AS count
FROM lead_state
GROUP BY campaign_name, status
ORDER BY campaign_name, status;


-- =============================================================================
-- SECTION 2: Scheduled Job Inspection
-- =============================================================================

-- 2a. All pending jobs for a specific contact
SELECT
    id,
    job_type,
    status,
    run_at,
    payload_json->>'contact_id' AS contact_id,
    payload_json->>'campaign_name' AS campaign_name,
    version
FROM scheduled_jobs
WHERE payload_json->>'contact_id' = '<contact_id>'
  AND status IN ('pending', 'claimed', 'running')
ORDER BY run_at ASC;


-- 2b. All pending outbound call jobs
SELECT
    id,
    payload_json->>'contact_id' AS contact_id,
    payload_json->>'phone_number' AS phone,
    payload_json->>'campaign_name' AS campaign_name,
    payload_json->>'vm_retry_attempt' AS attempt,
    run_at,
    created_at
FROM scheduled_jobs
WHERE job_type = 'launch_outbound_call'
  AND status = 'pending'
ORDER BY run_at ASC;


-- 2c. Failed jobs in the last 24 hours
SELECT
    id,
    job_type,
    entity_type,
    entity_id,
    payload_json->>'contact_id' AS contact_id,
    status,
    updated_at
FROM scheduled_jobs
WHERE status = 'failed'
  AND updated_at >= NOW() - INTERVAL '24 hours'
ORDER BY updated_at DESC;


-- 2d. Detect duplicate pending jobs for the same contact + job_type
-- (should be zero for well-behaved idempotency guards)
SELECT
    payload_json->>'contact_id' AS contact_id,
    job_type,
    COUNT(*) AS duplicate_count
FROM scheduled_jobs
WHERE status IN ('pending', 'claimed', 'running')
GROUP BY payload_json->>'contact_id', job_type
HAVING COUNT(*) > 1
ORDER BY duplicate_count DESC;


-- 2e. Job throughput by type in the last hour
SELECT
    job_type,
    status,
    COUNT(*) AS count,
    MIN(updated_at) AS oldest,
    MAX(updated_at) AS newest
FROM scheduled_jobs
WHERE updated_at >= NOW() - INTERVAL '1 hour'
GROUP BY job_type, status
ORDER BY job_type, status;


-- 2f. Stale claimed jobs (lease expired — may need recovery)
SELECT
    id,
    job_type,
    claimed_by,
    claimed_at,
    lease_expires_at,
    payload_json->>'contact_id' AS contact_id
FROM scheduled_jobs
WHERE status = 'claimed'
  AND lease_expires_at < NOW()
ORDER BY lease_expires_at ASC;


-- =============================================================================
-- SECTION 3: Call Event Inspection
-- =============================================================================

-- 3a. All call events for a contact
SELECT
    id,
    call_id,
    direction,
    status,
    duration_seconds,
    transcript,
    created_at
FROM call_events
WHERE contact_id = '<contact_id>'
ORDER BY created_at DESC;


-- 3b. Voicemail call events with transcripts (for intent review)
SELECT
    call_id,
    contact_id,
    status,
    LEFT(transcript, 200) AS transcript_preview,
    created_at
FROM call_events
WHERE status IN ('voicemail', 'hangup_on_voicemail', 'left_voicemail',
                 'voicemail_detected', 'machine_detected')
  AND transcript IS NOT NULL
  AND transcript != ''
ORDER BY created_at DESC
LIMIT 50;


-- 3c. Duplicate call event dedupe check
-- (uq_call_events_dedupe_key constraint should prevent these, but verify)
SELECT
    dedupe_key,
    COUNT(*) AS count
FROM call_events
GROUP BY dedupe_key
HAVING COUNT(*) > 1;


-- =============================================================================
-- SECTION 4: Exception Record Inspection
-- =============================================================================

-- 4a. All unresolved exceptions
SELECT
    id,
    type,
    severity,
    entity_type,
    entity_id,
    context,
    created_at
FROM exceptions
WHERE resolved_at IS NULL
ORDER BY severity DESC, created_at DESC;


-- 4b. Exceptions for a specific contact
SELECT
    id,
    type,
    severity,
    context,
    created_at,
    resolved_at
FROM exceptions
WHERE entity_id = '<contact_id>'
ORDER BY created_at DESC;


-- 4c. Critical exceptions in the last 7 days
SELECT
    type,
    COUNT(*) AS count,
    MAX(created_at) AS most_recent
FROM exceptions
WHERE severity = 'critical'
  AND created_at >= NOW() - INTERVAL '7 days'
GROUP BY type
ORDER BY count DESC;


-- =============================================================================
-- SECTION 5: Campaign Switch Audit
-- =============================================================================

-- 5a. Leads that switched from New Lead to Cold Lead
-- (campaign_name = 'Cold Lead' but were likely New Lead before)
-- Note: no history table — version column tracks mutation count only.
-- Use audit_log if available.
SELECT
    contact_id,
    campaign_name,
    ai_campaign_value,
    status,
    version,
    updated_at
FROM lead_state
WHERE campaign_name = 'Cold Lead'
ORDER BY updated_at DESC;


-- 5b. Leads that appear to have been reactivated
-- (Cold Lead with ai_campaign_value reset to None or '0' after a higher tier)
-- This is an anomaly check — normal flow only advances tier upward.
SELECT
    l.contact_id,
    l.campaign_name,
    l.ai_campaign_value,
    l.version,
    l.updated_at
FROM lead_state l
WHERE l.campaign_name = 'New Lead'
  AND l.ai_campaign_value IS NULL
  AND l.version > 2   -- has been updated multiple times
ORDER BY l.updated_at DESC;


-- =============================================================================
-- SECTION 6: Intent Action Summary
-- =============================================================================

-- 6a. Leads by final status (summary of intent-driven outcomes)
SELECT
    status,
    campaign_name,
    COUNT(*) AS count
FROM lead_state
WHERE status IN ('enrolled', 'closed', 'nurture', 'active')
GROUP BY status, campaign_name
ORDER BY status, campaign_name;


-- 6b. Leads with preferred_channel set (SMS/email channel preference recorded)
SELECT
    contact_id,
    preferred_channel,
    campaign_name,
    status,
    updated_at
FROM lead_state
WHERE preferred_channel IS NOT NULL
ORDER BY updated_at DESC;


-- =============================================================================
-- SECTION 7: System Health Checks
-- =============================================================================

-- 7a. Job queue depth by status (snapshot of current backlog)
SELECT
    status,
    job_type,
    COUNT(*) AS count,
    MIN(run_at) AS oldest_run_at,
    MAX(run_at) AS newest_run_at
FROM scheduled_jobs
WHERE status NOT IN ('completed', 'cancelled')
GROUP BY status, job_type
ORDER BY status, job_type;


-- 7b. Jobs overdue (run_at in the past, still pending)
SELECT
    id,
    job_type,
    run_at,
    payload_json->>'contact_id' AS contact_id,
    NOW() - run_at AS overdue_by
FROM scheduled_jobs
WHERE status = 'pending'
  AND run_at < NOW() - INTERVAL '5 minutes'
ORDER BY run_at ASC
LIMIT 50;
