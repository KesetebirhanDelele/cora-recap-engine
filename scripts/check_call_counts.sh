#!/usr/bin/env bash
# check_call_counts.sh — run from the repo root on the Hetzner server
# Usage: bash scripts/check_call_counts.sh

set -euo pipefail

SQL="
SELECT
  date_trunc('week', created_at)::date                          AS week_start,
  COUNT(*)                                                       AS total_calls,
  COUNT(DISTINCT
    CASE
      WHEN lower(direction) = 'inbound'
        THEN raw_payload_json->>'phone_number_from'
      ELSE raw_payload_json->>'phone_number_to'
    END
  )                                                              AS unique_contacts,
  COUNT(*) FILTER (WHERE voice_agent = 'ColdLead')              AS cold_lead_calls,
  COUNT(*) FILTER (WHERE voice_agent = 'NewLead')               AS new_lead_calls,
  COUNT(*) FILTER (WHERE voice_agent = 'Inbound')               AS inbound_calls,
  MAX(created_at)::date                                         AS latest_call_date
FROM call_events
WHERE created_at >= NOW() - INTERVAL '35 days'
GROUP BY date_trunc('week', created_at)
ORDER BY week_start DESC;
"

echo "=== Call counts by week (last 5 weeks) ==="
docker compose exec -T postgres psql \
  -U "${POSTGRES_USERNAME:-postgres}" \
  -d "${POSTGRES_DATABASE:-cora}" \
  -c "$SQL"

echo ""
echo "=== Latest call in DB ==="
docker compose exec -T postgres psql \
  -U "${POSTGRES_USERNAME:-postgres}" \
  -d "${POSTGRES_DATABASE:-cora}" \
  -c "SELECT MAX(created_at) AS latest_call, COUNT(*) AS total_all_time FROM call_events;"
