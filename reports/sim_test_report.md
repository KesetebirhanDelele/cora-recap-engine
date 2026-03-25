# Simulation Test Report — Cora Recap Engine

**Generated:** 2026-03-24 22:53 UTC  
**Webhook URL:** http://localhost:8000  
**Scenarios:** 1  **Passed:** 1  **Failed:** 0

---

## Summary

| ID | Scenario | Result | Duration |
|----|----------|--------|----------|
| SC10 | Reply Stops Messaging | ✅ PASS | 22.8s |

---

## SC10 — Reply Stops Messaging  ✅ PASS

**Contact:** `sim-sc10-001`  
**Started:** 22:52:42 UTC  
**Duration:** 22.8s  

### Step Results

- `[22:52:43]` **✓ voicemail (SMS will be scheduled)** — HTTP 202
- `[22:52:43]` **✓ wait:process_voicemail_tier** — job_id=90e451f4-540d-445f-88d6-56281c93e2e6
- `[22:52:43]` **✓ assert:send_sms_scheduled** — job_id=08901805-2492-4fc4-86c1-cd65fb4b2916
- `[22:52:43]` **✓ simulate:reply** — InboundMessage row inserted
- `[22:52:43]` **✓ fast_forward:send_sms** — run_at → NOW
- `[22:53:05]` **✓ wait:send_sms_completed** — job_id=08901805-2492-4fc4-86c1-cd65fb4b2916
- `[22:53:05]` **✓ assert:no_outbound_sms** — outbound_messages[channel=sms] count=0 (expected 0 — suppressed)

### Notes

> Reply suppression check runs BEFORE OpenAI call in send_sms_job — works without OPENAI_API_KEY.

### Verification Queries

```sql
-- Lead state
SELECT contact_id, campaign_name, ai_campaign_value, status, next_action_at
FROM lead_state WHERE contact_id = 'sim-sc10-001';

-- Jobs
SELECT job_type, status, run_at, payload_json
FROM scheduled_jobs
WHERE payload_json->>'contact_id' = 'sim-sc10-001'
ORDER BY created_at DESC;

-- Call events
SELECT contact_id, status, LEFT(transcript,120) AS transcript, created_at
FROM call_events WHERE contact_id = 'sim-sc10-001';

-- Exceptions
SELECT type, severity, entity_id, created_at
FROM exceptions WHERE entity_id = 'sim-sc10-001';
```

---
