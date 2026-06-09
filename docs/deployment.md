# Deploying a Dockerized Project to Hetzner Cloud

This document describes the general procedure for deploying a multi-service Docker Compose application to a Hetzner Cloud VPS. All section headings marked with `[CUSTOMIZE]` contain project-specific values that must be updated for each deployment.

---

## Prerequisites

- A [Hetzner Cloud](https://console.hetzner.cloud/) account
- A project repository with a `docker-compose.yml` and at least one `Dockerfile`
- SSH key registered in Hetzner Cloud
- Domain or static IP for the project (domain optional for early deployments)

---

## Step 1 — Provision the Server

### 1.1 Create a Cloud VM

In the Hetzner Cloud Console:

1. **New Server** → choose a datacenter region (e.g., `nbg1` Nuremberg or `fsn1` Falkenstein)
2. **Image**: Ubuntu 22.04 LTS
3. **Type** `[CUSTOMIZE]`: Start with the smallest instance that fits your workload:
   - `CX22` — 2 vCPU / 4 GB RAM — suitable for light workloads
   - `CPX21` — 3 vCPU / 4 GB RAM — better under sustained API load
   - `CPX31` — 4 vCPU / 8 GB RAM — multiple workers + DB on same host
4. **SSH Keys**: add your public key
5. **Firewall**: create a firewall rule set (see §1.2)
6. Click **Create & Buy**

### 1.2 Configure Firewall Rules

Create a Hetzner Firewall and attach it to the server. Open inbound TCP on the ports your services expose. Example baseline:

| Port | Protocol | Source    | Purpose                        |
|------|----------|-----------|--------------------------------|
| 22   | TCP      | Your IP   | SSH access (restrict to your IP) |
| 80   | TCP      | Any       | HTTP (redirect to HTTPS if using Caddy/Nginx) |
| 443  | TCP      | Any       | HTTPS (reverse proxy)          |
| `[CUSTOMIZE]` | TCP | Any | Public service ports (API, frontend, etc.) |

> **Do not expose** database ports (5432), Redis (6379), or admin tools (e.g., Adminer on 8080) to the public internet. Use SSH tunnels for local access.

---

## Step 2 — Prepare the Server

SSH into the server as root (or a sudo user):

```bash
ssh root@<server-ip>
```

### 2.1 Install Docker CE

```bash
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Verify:

```bash
docker --version
docker compose version
```

### 2.2 Create a Non-Root Deploy User (Recommended)

```bash
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
```

From now on, SSH as `deploy` rather than `root`.

---

## Step 3 — Deploy the Application

### 3.1 Clone the Repository

```bash
# [CUSTOMIZE] — replace with your repo URL and target directory
git clone https://github.com/<org>/<repo>.git /opt/<project-name>
cd /opt/<project-name>
```

### 3.2 Create the Production Environment File

Copy the environment template and fill in all values:

```bash
# [CUSTOMIZE] — name of your example file may differ
cp .env.production.example .env
nano .env   # or use vim / any editor
```

Critical variables to set `[CUSTOMIZE]`:

```dotenv
# Application
APP_ENV=production          # Must be "production" — prevents dev-only routes from loading

# Database credentials — generate strong random passwords
POSTGRES_USER=<db_user>
POSTGRES_PASSWORD=<strong_random_password>
POSTGRES_DB=<db_name>
DATABASE_URL=postgresql://<db_user>:<password>@pgbouncer:5432/<db_name>

# Redis
REDIS_HOST=redis            # Docker internal hostname; do not change if using Compose

# Public URLs — used at build time for frontend assets
# [CUSTOMIZE] — set to your server IP or domain
DASHBOARD_API_URL=http://<server-ip>:<api-port>
WS_URL=ws://<server-ip>:<api-port>
ALLOW_ORIGINS=http://<server-ip>:<frontend-port>

# External service API keys [CUSTOMIZE]
SOME_API_KEY=<value>
ANOTHER_SECRET=<value>
```

> **Security rule**: Never commit `.env` to Git. Verify `.gitignore` lists `.env` before the first deploy.

### 3.3 Build and Start Services

```bash
cd /opt/<project-name>
docker compose up -d --build
```

This will:
1. Build all images defined in `docker-compose.yml`
2. Start every service in the correct dependency order
3. Run one-shot jobs (e.g., database migrations) before the app starts

> **Note**: Code is baked into Docker images at build time. A plain `docker compose restart <service>` does **not** pick up code changes. Always use `--build` after a code update.

### 3.4 Verify the Deploy

```bash
# All services should show "Up" or "healthy"
docker compose ps

# Check that the migration job exited cleanly (exit code 0)
docker compose logs migrate

# Tail live logs from a specific service [CUSTOMIZE]
docker compose logs -f api
docker compose logs -f worker-default
```

---

## Step 4 — Updating the Application

For every subsequent code deploy:

```bash
cd /opt/<project-name>
git pull origin main                       # or your production branch
docker compose up -d --build               # rebuild changed images, restart services
docker compose logs migrate                # confirm migrations succeeded
docker compose ps                          # confirm all services running
```

If Docker's layer cache is serving stale code (rare but possible):

```bash
# [CUSTOMIZE] — replace <service> with the affected service name
docker compose build --no-cache <service>
docker compose up -d --no-deps <service>
```

---

## Step 5 — Reverse Proxy and HTTPS (Optional but Recommended)

For production deployments exposed to end users, add a reverse proxy in front of your services to handle TLS termination, domain routing, and port consolidation.

### Option A — Caddy (simplest, automatic HTTPS)

Install Caddy on the host (or add it as a Docker Compose service), then write a `Caddyfile`:

```
# [CUSTOMIZE]
yourdomain.com {
    reverse_proxy localhost:<frontend-port>
}

api.yourdomain.com {
    reverse_proxy localhost:<api-port>
}
```

Caddy automatically provisions and renews Let's Encrypt certificates.

### Option B — Nginx

```nginx
# [CUSTOMIZE] /etc/nginx/sites-available/<project>
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:<frontend-port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Use Certbot for certificate management:

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d yourdomain.com
```

---

## Step 6 — Data Persistence and Backups

Docker named volumes survive image rebuilds. The database data lives in a named volume defined in `docker-compose.yml`:

```yaml
# [CUSTOMIZE] — check your docker-compose.yml for the actual volume name
volumes:
  postgres_data:
```

**Backup before any destructive operation:**

```bash
# Dump the database from inside the Postgres container [CUSTOMIZE]
docker compose exec postgres pg_dump -U <db_user> <db_name> > backup_$(date +%Y%m%d).sql
```

**Destroy everything including data** (use with caution):

```bash
docker compose down -v   # -v removes named volumes — irreversible
```

**Restore from a dump:**

```bash
cat backup_20260101.sql | docker compose exec -T postgres psql -U <db_user> <db_name>
```

---

## Step 7 — Scaling

### Vertical Scaling

Upgrade the Hetzner VM type in the console (requires a server restart):

```
CX22 (2 vCPU / 4 GB)  →  CPX21  →  CPX31  →  CPX41  →  CCX series
```

After resize, services restart automatically when the VM comes back up.

### Horizontal Worker Scaling

If your workers use a claim/lease job pattern (idempotent job execution), you can run multiple replicas with no code changes:

```bash
# [CUSTOMIZE] — replace worker-default with your worker service name
docker compose up --scale worker-default=3 -d
```

### Connection Pooling

Add PgBouncer between application services and Postgres when the number of Postgres connections becomes a bottleneck. Set `pool_mode = transaction` and configure max connections in `pgbouncer.ini`. Application services connect to `pgbouncer:5432` instead of `postgres:5432`. The migration service should always connect directly to `postgres:5432` (DDL safety).

---

## Step 8 — Common Operational Commands

```bash
# View running services
docker compose ps

# Follow logs for a service [CUSTOMIZE]
docker compose logs -f <service-name>

# Open a shell inside a running container [CUSTOMIZE]
docker compose exec <service-name> bash

# Run a database query (psql is not in app containers — use the postgres container)
docker compose exec postgres psql -U <db_user> -d <db_name>

# Force-rebuild one service without rebuilding others
docker compose build --no-cache <service>
docker compose up -d --no-deps <service>

# Restart a service (no code reload — for config-only changes)
docker compose restart <service>

# Stop everything (keeps volumes)
docker compose down

# Stop everything and wipe data (IRREVERSIBLE)
docker compose down -v
```

---

## Deployment Checklist

Use this before every production deploy:

- [ ] `.env` file is present on the server and not committed to Git
- [ ] `APP_ENV=production` is set
- [ ] Database credentials are strong and unique
- [ ] All external API keys and secrets are populated
- [ ] Public-facing URLs in `.env` match the actual server IP or domain
- [ ] Firewall rules block database and admin ports from public access
- [ ] `docker compose ps` shows all services healthy after deploy
- [ ] Migration logs show clean exit (`Alembic upgrade head` with no errors)
- [ ] A database backup exists before any schema-changing migration
- [ ] `.gitignore` includes `.env`, logs, and any generated artifacts

---

## Architecture Reference

The pattern used in this project and this guide follows a **single-host Docker Compose** topology:

```
Internet
  │
  ├─ :80/:443  → Reverse Proxy (Caddy/Nginx) [optional]
  │                │
  │                ├─ Frontend        (Next.js / static)
  │                └─ API             (FastAPI / Express / etc.)
  │
  ├─ :XXXX    → Direct port access (pre-proxy phase)
  │
Hetzner VM
  ├── api              (web server)
  ├── dashboard-api    (secondary API, optional)
  ├── frontend         (SSR or static frontend)
  ├── worker-*         (background job workers)
  ├── postgres         (database, internal only)
  ├── redis            (queue broker, internal only)
  ├── pgbouncer        (connection pooler, optional)
  └── adminer          (DB admin, localhost only)
```

All services communicate over a Docker Compose internal network. Only explicitly mapped ports are reachable from the host.
