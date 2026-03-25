-- =============================================================================
-- seed_scenarios.sql — Production simulation seed data
-- =============================================================================
-- Inserts one lead per scenario into lead_state using deterministic contact_ids
-- so scenario_runner.py can locate them by id.
--
-- Run ONCE before first use:
--   psql -h localhost -p 5433 -U postgres -d cora -f execution/seed_scenarios.sql
--
-- The scenario_runner.py also performs its own upsert/clean per contact,
-- so this file is optional if --clean is passed to the runner.
--
-- Phone numbers follow E.164 format.
-- All leads start with status='active', ai_campaign_value=NULL (no voicemails yet).
-- =============================================================================

BEGIN;

-- Remove any prior simulation rows for these contact_ids (idempotent re-seed)
DELETE FROM outbound_messages  WHERE contact_id LIKE 'sim-sc%';
DELETE FROM inbound_messages   WHERE contact_id LIKE 'sim-sc%';
DELETE FROM exceptions         WHERE entity_id  LIKE 'sim-sc%';
DELETE FROM scheduled_jobs
    WHERE payload_json::text LIKE '%sim-sc%'
       OR entity_id LIKE 'sim-sc%';
DELETE FROM call_events        WHERE contact_id LIKE 'sim-sc%';
DELETE FROM lead_state         WHERE contact_id LIKE 'sim-sc%';

-- ---------------------------------------------------------------------------
-- SC1 — Voicemail Retry Ladder (New Lead, starts at tier NULL)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc1-001',
    '+15551110001',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC2 — Interested Not Now (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc2-001',
    '+15551110002',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC3 — Uncertain (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc3-001',
    '+15551110003',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC4 — Callback Request (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc4-001',
    '+15551110004',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC5 — Not Interested (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc5-001',
    '+15551110005',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC6 — Enrollment / Campaign Exit (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc6-001',
    '+15551110006',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC7 — Duplicate Call Protection (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc7-001',
    '+15551110007',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC8 — Multi-Step Journey (New Lead, starts clean)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc8-001',
    '+15551110008',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC9 — SMS + Email Scheduling (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc9-001',
    '+15551110009',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

-- ---------------------------------------------------------------------------
-- SC10 — Reply Stops Messaging (New Lead)
-- ---------------------------------------------------------------------------
INSERT INTO lead_state (
    id, contact_id, normalized_phone, campaign_name,
    ai_campaign_value, status, do_not_call, invalid, version,
    created_at, updated_at
) VALUES (
    gen_random_uuid()::text,
    'sim-sc10-001',
    '+15551110010',
    'New Lead',
    NULL, 'active', FALSE, FALSE, 0,
    NOW(), NOW()
);

COMMIT;

-- Verify seed
SELECT contact_id, campaign_name, ai_campaign_value, status
FROM lead_state
WHERE contact_id LIKE 'sim-sc%'
ORDER BY contact_id;
