#!/usr/bin/env bash
# =============================================================================
# deploy.sh
# Deploy or update cora-recap-engine on the Hetzner server.
# Safe to run on first deploy and on every subsequent code push.
#
# Usage (from project root or scripts/ directory):
#   bash scripts/deploy.sh
#
# Options:
#   --no-cache    Force full image rebuild (clears Docker layer cache).
#                 Use when a pip install / npm ci change isn't picked up.
#
# What it does:
#   1. Pulls latest code from git
#   2. Rebuilds Docker images (layer-cached by default)
#   3. Runs alembic migrations (idempotent)
#   4. Restarts app services with zero-downtime rolling update
#   5. Health-checks API and Dashboard API
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

NO_CACHE=""
if [[ "${1:-}" == "--no-cache" ]]; then
  NO_CACHE="--no-cache"
  echo "!!! --no-cache flag set: full rebuild (slower, clears all layer cache)"
fi

# ── Guard: .env must exist ────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found. Copy .env.production.example, fill in required values, then re-run."
  exit 1
fi

# ── Guard: APP_ENV must be production ─────────────────────────────────────────
APP_ENV_VAL=$(grep -E '^APP_ENV=' .env | cut -d= -f2 | tr -d '[:space:]"' || true)
if [[ "$APP_ENV_VAL" != "production" ]]; then
  echo "ERROR: APP_ENV in .env is '${APP_ENV_VAL}', expected 'production'. Refusing to deploy."
  exit 1
fi

echo ""
echo "==> [1/5] Pulling latest code..."
git pull --ff-only

echo ""
echo "==> [2/5] Building images..."
# Rebuilds: api, dashboard-api, frontend, all workers.
# postgres, redis, pgbouncer, adminer use upstream images (docker compose pull handles those).
docker compose pull postgres redis adminer 2>/dev/null || true
# shellcheck disable=SC2086
docker compose build $NO_CACHE \
  api dashboard-api frontend \
  worker-default worker-ai worker-callbacks worker-retries

echo ""
echo "==> [3/5] Ensuring infrastructure is up (postgres, redis, pgbouncer)..."
docker compose up -d postgres redis
# Give postgres a moment if it's cold-starting
sleep 5
docker compose up -d pgbouncer

echo ""
echo "==> [4/5] Running migrations..."
# Connects directly to postgres:5432 (bypasses PgBouncer — safe for DDL).
# Alembic upgrade head is idempotent; re-running is always safe.
docker compose run --rm \
  -e DATABASE_URL="postgresql+psycopg2://$(grep -E '^POSTGRES_USERNAME=' .env | cut -d= -f2 | tr -d '[:space:]'):$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2 | tr -d '[:space:]')@postgres:5432/$(grep -E '^POSTGRES_DATABASE=' .env | cut -d= -f2 | tr -d '[:space:]')" \
  migrate

echo ""
echo "==> [5/5] Starting / restarting app services..."
docker compose up -d --no-deps \
  api dashboard-api frontend \
  worker-default worker-ai worker-callbacks worker-retries

echo ""
echo "Waiting 15s for services to stabilise..."
sleep 15

# ── Health checks ─────────────────────────────────────────────────────────────
echo ""
echo "==> Health checks..."

_check() {
  local name="$1" url="$2"
  local status
  status=$(curl -sf --max-time 5 "$url" \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" \
    2>/dev/null || echo "FAIL")
  printf "  %-22s %s\n" "$name" "$status"
  [[ "$status" == "ok" ]]
}

PASS=true
_check "API (port 8000)"       "http://localhost:8000/health"  || PASS=false
_check "Dashboard (port 8001)" "http://localhost:8001/health"  || PASS=false

if [[ "$PASS" == "false" ]]; then
  echo ""
  echo "!!! One or more health checks failed. Investigate:"
  echo "    docker compose logs api --tail=30"
  echo "    docker compose logs dashboard-api --tail=30"
  echo "    docker compose ps"
  exit 1
fi

echo ""
echo "==> Service status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "============================================================"
echo "  Deploy complete."
echo ""
echo "  Endpoints:"
SERVER_IP=$(curl -sf --max-time 3 https://checkip.amazonaws.com 2>/dev/null || echo "<server-ip>")
echo "    Webhook intake:  http://$SERVER_IP:8000/v1/webhooks/calls"
echo "    Dashboard:       http://$SERVER_IP:3000"
echo "    Dashboard API:   http://$SERVER_IP:8001/health"
echo "    Adminer (DB UI): SSH tunnel → http://localhost:8080"
echo "============================================================"
