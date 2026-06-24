#!/usr/bin/env bash
# =============================================================================
# setup-server.sh
# One-time bootstrap for a fresh Hetzner Ubuntu 22.04 server.
#
# Usage (as root on the server):
#   REPO_URL=https://github.com/<org>/cora-recap-engine bash setup-server.sh
#
# What it does:
#   1. Installs Docker CE + Compose plugin
#   2. Configures ufw firewall (SSH + app ports)
#   3. Clones the repo to /opt/cora-recap-engine
#   4. Creates .env from the production example
#
# After this script completes, fill in /opt/cora-recap-engine/.env then run:
#   bash /opt/cora-recap-engine/scripts/deploy.sh
# =============================================================================
set -euo pipefail

REPO_URL="${REPO_URL:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/cora-recap-engine}"

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: Set REPO_URL before running this script."
  echo "  REPO_URL=https://github.com/<org>/cora-recap-engine bash setup-server.sh"
  exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: Run as root."
  exit 1
fi

echo ""
echo "==> [1/4] Installing Docker CE..."
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
docker --version
docker compose version

echo ""
echo "==> [2/4] Configuring firewall (ufw)..."
ufw allow OpenSSH
ufw allow 8000/tcp   # Synthflow webhook intake (must be reachable from internet)
ufw allow 8001/tcp   # Dashboard API (browser-accessible)
ufw allow 3000/tcp   # Next.js frontend (browser-accessible)
# Port 8080 (Adminer) is intentionally NOT opened — access via SSH tunnel only.
ufw --force enable
ufw status

echo ""
echo "==> [3/4] Cloning repository to $DEPLOY_DIR ..."
if [[ -d "$DEPLOY_DIR/.git" ]]; then
  echo "    Repo already exists — skipping clone."
else
  git clone "$REPO_URL" "$DEPLOY_DIR"
fi
cd "$DEPLOY_DIR"

echo ""
echo "==> [4/4] Creating .env from .env.production.example ..."
if [[ -f "$DEPLOY_DIR/.env" ]]; then
  echo "    .env already exists — skipping copy (edit it manually if needed)."
else
  cp "$DEPLOY_DIR/.env.production.example" "$DEPLOY_DIR/.env"
  echo "    Created $DEPLOY_DIR/.env"
fi

echo ""
echo "============================================================"
echo "  Server setup complete."
echo ""
echo "  NEXT STEP: fill in all REQUIRED values in .env:"
echo "    nano $DEPLOY_DIR/.env"
echo ""
echo "  Minimum required values:"
echo "    SECRET_KEY           — openssl rand -hex 32"
echo "    WEBHOOK_SHARED_SECRET — openssl rand -hex 32"
echo "    POSTGRES_PASSWORD    — strong random password"
echo "    GHL_API_KEY"
echo "    GHL_LOCATION_ID"
echo "    SYNTHFLOW_API_KEY"
echo "    SYNTHFLOW_MODEL_ID"
echo "    OPENAI_API_KEY"
echo "    DASHBOARD_API_URL    — http://<server-ip>:8001"
echo "    WS_URL               — ws://<server-ip>:8001/ws"
echo "    ALLOW_ORIGINS        — http://<server-ip>:3000"
echo ""
echo "  Then deploy:"
echo "    bash $DEPLOY_DIR/scripts/deploy.sh"
echo "============================================================"
