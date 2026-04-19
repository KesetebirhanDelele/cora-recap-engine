#!/usr/bin/env bash
# import_calldate.sh — run from the repo root on the Hetzner server.
#
# Usage:
#   1. SCP the CSV to the server first:
#      scp calldate_import.csv root@<server>:/opt/cora-recap-engine/scripts/
#   2. Then on the server:
#      bash scripts/import_calldate.sh
#
# What it does:
#   1. Runs the Alembic migration to add the 3 new columns.
#   2. Copies the CSV into a temp table inside Postgres.
#   3. Updates call_events by joining on call_id.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CSV_FILE="$SCRIPT_DIR/calldate_import.csv"
PG_USER="${POSTGRES_USERNAME:-postgres}"
PG_DB="${POSTGRES_DATABASE:-cora}"

if [[ ! -f "$CSV_FILE" ]]; then
  echo "ERROR: $CSV_FILE not found. SCP it to scripts/ first."
  exit 1
fi

echo "=== Step 1: Run Alembic migration (adds 3 columns) ==="
docker compose exec -T migrate alembic upgrade head

echo ""
echo "=== Step 2: Copy CSV into Postgres temp table ==="

# Copy the CSV into the container's /tmp directory
docker compose cp "$CSV_FILE" postgres:/tmp/calldate_import.csv

# Create temp table, COPY data in, run UPDATE JOIN, then drop temp table
docker compose exec -T postgres psql -U "$PG_USER" -d "$PG_DB" <<'SQL'
BEGIN;

CREATE TEMP TABLE _calldate_import (
    call_id            TEXT,
    synthflow_start_ms BIGINT,
    call_timezone      TEXT,
    call_time_local    TEXT
) ON COMMIT DROP;

COPY _calldate_import (call_id, synthflow_start_ms, call_timezone, call_time_local)
FROM '/tmp/calldate_import.csv'
WITH (FORMAT csv, HEADER true, NULL '');

UPDATE call_events ce
SET
    synthflow_start_ms = src.synthflow_start_ms,
    call_timezone      = src.call_timezone,
    call_time_local    = src.call_time_local
FROM _calldate_import src
WHERE ce.call_id = src.call_id;

COMMIT;

SELECT
    COUNT(*)                                            AS total_rows_in_file,
    COUNT(*) FILTER (WHERE synthflow_start_ms IS NOT NULL) AS matched_and_updated
FROM call_events
WHERE synthflow_start_ms IS NOT NULL
   OR call_timezone IS NOT NULL;
SQL

echo ""
echo "=== Done ==="
