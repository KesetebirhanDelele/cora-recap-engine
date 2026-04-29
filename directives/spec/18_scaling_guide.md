# spec/18_scaling_guide.md
# Scaling Guide — Cora Recap Engine

This directive is the authoritative reference for scaling decisions. Read it before any infrastructure change
that affects capacity, throughput, or availability. Update it when a scaling action is taken or a new
bottleneck is identified.

---

## Architecture components and their scaling axes

| Component | Current deployment | Stateful? | Scales horizontally? |
|---|---|---|---|
| FastAPI main API (port 8000) | Single container | No | Yes — no code changes needed |
| FastAPI dashboard API (port 8001) | Single container | No | Yes — no code changes needed |
| Worker | Single container | No | Yes — claim/lease pattern already concurrency-safe |
| PostgreSQL | Single container, named volume | Yes | Read replicas only; write path is single-primary |
| Redis | Single container | Soft (pub/sub) | Redis Cluster or Sentinel for HA |
| Next.js frontend (port 3000) | Single container | No | Yes — stateless SSR |
| Streamlit dashboard | Optional, single process | No | Not a production path; ignore for scaling |

---

## Bottleneck priority order

Address bottlenecks in this order. Later stages rarely apply until earlier ones are exhausted.

### Stage 1 — Vertical (no ops complexity)
**Signal:** API p95 latency > 500 ms, worker job lag rising, Postgres CPU > 60 %.

**Actions:**
- Upgrade Hetzner server (CPX21 → CPX31 → CPX41).
- Tune Postgres memory:
  ```
  shared_buffers = 25% of total RAM
  work_mem = 16MB–64MB (raise carefully; multiplies per sort/hash)
  max_connections = 100 (use PgBouncer instead of raising this)
  effective_cache_size = 75% of total RAM
  ```
- Increase Docker memory limits in `docker-compose.yml` for `postgres` and `api`.

### Stage 2 — Worker horizontal scale (zero code changes)
**Signal:** `queue_lag_seconds` alert fires; `stuck_job_count` consistently non-zero; Postgres and Redis healthy.

**Action:** Add worker replicas via Compose scale:
```bash
cd /opt/cora-recap-engine
docker compose up -d --scale worker=3
```

The claim/lease pattern in `app/worker/claim.py` is already concurrency-safe:
- `UPDATE ... WHERE version = expected` prevents double-claim (optimistic lock).
- `recover_expired_claims` auto-heals crashed worker leases.
- `has_pending_callback()` prevents duplicate Synthflow calls across workers.

**Ceiling before external API rate limits become the real constraint:** ~5–8 workers for current GHL/Synthflow/OpenAI quotas.

### Stage 3 — PgBouncer connection pooler
**Status: IMPLEMENTED 2026-04-29**

**Signal:** Postgres `max_connections` near limit; API or worker logs show connection wait time.

**Implemented config** (`docker-compose.yml`):
- Image: `bitnami/pgbouncer:latest`
- Pool mode: `transaction` — Postgres connections recycled after each commit
- Max client connections: 500
- Default pool size: 20 actual Postgres connections
- Listen port: 5432 (internal Docker network)
- Auth type: `md5`
- `PGBOUNCER_IGNORE_STARTUP_PARAMETERS: extra_float_digits` — prevents SQLAlchemy startup param warnings

**Routing:**
- All app services (api, dashboard-api, worker-*): `DATABASE_URL` → `pgbouncer:5432`
- `migrate` and `adminer`: connect directly to `postgres:5432` (bypass PgBouncer — DDL safety + direct admin access)

**Effective ceiling after this change:** Postgres sees max 20 connections regardless of how many app services, worker replicas, or admin tools are running simultaneously. Connection exhaustion is no longer a concern at current or projected scale.

**If PgBouncer fails to start** (auth errors at startup):
- Check `docker compose logs pgbouncer` for `auth_query failed` or `password mismatch`
- Confirm `POSTGRES_PASSWORD` in `.env` matches what Postgres was initialized with
- Fallback: revert app services to `postgres:5432` in DATABASE_URL, remove pgbouncer depends_on

### Stage 4 — API horizontal scale + load balancer
**Signal:** API CPU > 70 % sustained; vertical upgrade not cost-effective; need zero-downtime deploys.

**Action:** Add Nginx (or Caddy) in front of multiple API replicas:
```
nginx:443
  ├── api-1:8000
  ├── api-2:8000
  └── api-3:8000
```

**Prerequisites before scaling API horizontally:**
- `SECRET_KEY` must be identical across all replicas (already env-driven — no code change).
- `ALLOW_ORIGINS` must list the load balancer's external hostname.
- `DASHBOARD_API_URL` in the frontend build arg must point to the load balancer, not a single host IP.
- WebSocket (`/dashboard/ws/events`) must be sticky-routed to one dashboard-api instance per client, OR Redis pub/sub fanout must be confirmed working across all instances (it already is — each dashboard-api instance subscribes to `dashboard:events` independently).

### Stage 5 — Postgres read replica
**Signal:** Postgres read load (dashboard queries, metrics aggregates) competes with write load (job claims, state transitions).

**Action:** Add a streaming replication replica. Route read-only queries to it.

**Code change required:** Add `get_read_session()` in `app/db.py` pointing to the replica URL. Update read-only paths:
- `app/services/dashboard_metrics.py` — all `get_*()` functions
- `app/api/routes/dashboard_v2.py` — GET endpoints
- `app/services/pipeline_trace.py`

Write paths (job claims, state transitions, audit log) must stay on the primary. Never route writes to a replica.

```python
# app/db.py addition
READ_DATABASE_URL = settings.read_database_url or settings.database_url

read_engine = create_engine(READ_DATABASE_URL, pool_pre_ping=True)
ReadSession = sessionmaker(bind=read_engine)

def get_read_session():
    with ReadSession() as s:
        yield s
```

### Stage 6 — Managed Postgres / Redis
**Signal:** DBA time exceeds engineering time; HA/failover needed; backup SLA required.

**Action:** Migrate to:
- **Hetzner Managed Databases** (Postgres + Redis) — stays in same datacenter, low latency.
- Or **RDS + ElastiCache** if migrating to AWS.

Update `DATABASE_URL` and `REDIS_URL` in `.env`. No code changes. Test migration with `alembic upgrade head` on the new host before cutting over.

---

## External API rate limits — scale ceiling

Horizontal worker scaling does not help when the bottleneck is an external API quota. Identify the binding
constraint before adding workers.

| API | Known limit | Mitigation |
|---|---|---|
| GHL (GoHighLevel) | ~100 req/s per location | Backoff + retry in `app/adapters/ghl.py`; queue GHL writes behind a rate-limited dispatcher |
| Synthflow | HTTP step concurrency limit (undocumented) — simultaneous webhook POSTs are dropped silently. Limit applies per-second, not per-slot. Confirmed: even 2–4 calls completing at the same second can trigger drops. | Never assign the same `run_at` to multiple calls. Use `_compute_window_run_at()` for all rescheduled calls — max 4 calls per 5-min slot, staggered 75 s apart within the slot (+0s, +75s, +150s, +225s). See `spec/14_synthflow_integration_addendum.md` for full details and recovery scripts. |
| OpenAI | Token/minute and request/minute limits | Exponential backoff already in `app/adapters/openai_client.py`; add per-minute token budget tracking for high volume |

**Rule:** Before scaling workers beyond 3, confirm external API throughput can absorb the increased call rate.

---

## Metrics to watch before and after scaling

All of these are visible on the dashboard at `http://<host>:3000`.

| Metric | Healthy range | Scale signal |
|---|---|---|
| `queue_lag_seconds` | < 60 s | > 300 s → add workers or investigate external API limits |
| `active_workers` | ≥ 1 | 0 → restart worker service |
| `stuck_job_count` | 0 | > 0 sustained → investigate job type; may need rate limit handling |
| `error_rate` | < 5 % | > 20 % → exception spike; root cause before scaling |
| Postgres connections (via `pg_stat_activity`) | < 80 % of `max_connections` | Add PgBouncer |

Direct Postgres query for connection pressure:
```bash
docker compose exec postgres psql -U postgres -d cora -c "
  SELECT count(*), state FROM pg_stat_activity GROUP BY state;"
```

---

## Data persistence across deploys

Postgres data lives in a named Docker volume (`postgres_data`). It is **never affected** by image rebuilds,
`docker compose up --build`, `docker compose restart`, or `docker compose down` (without `-v`).

**Destructive commands — never run in production without explicit backup:**
- `docker compose down -v` — removes all volumes including `postgres_data`
- `docker volume rm cora-recap-engine_postgres_data` — same effect

Safe commands (data always preserved):
```bash
docker compose up -d --build <service>   # rebuild + restart one service
docker compose restart <service>          # restart without rebuild
docker compose down                       # stop containers, volumes untouched
```

---

## Pre-scaling checklist

Before any scaling action, run through this checklist:

- [ ] Identify the actual bottleneck metric (queue lag, API latency, Postgres CPU, external API 429s)
- [ ] Confirm Postgres data is backed up (or snapshot the volume)
- [ ] Confirm `SECRET_KEY` and all environment variables are consistent across replicas
- [ ] For worker scale-up: verify `has_pending_callback()` behavior under concurrent workers in staging
- [ ] For API scale-up: confirm WebSocket sticky routing or Redis fanout is working
- [ ] For Postgres changes: run `alembic upgrade head` on new host before cutover
- [ ] Update this directive after the action is taken (record what was done, why, and the result)

---

## Scaling action log

Record each scaling action here so future engineers have a paper trail.

| Date | Action | Reason | Result |
|---|---|---|---|
| — | Initial single-host deploy | Baseline | — |
| 2026-04-27 | Added `_compute_window_run_at()` slot-based call spacing | 366 simultaneous calls at 9 AM CDT overwhelmed Synthflow HTTP step concurrency limit; webhooks dropped | Fixed — 10 calls/slot, 2-min slots. Manually recovered 134 finalized leads + 450 rescheduled leads. |
| 2026-04-29 | Tightened burst cap: `_CALL_BATCH_SIZE` 10→4, `_CALL_SLOT_SECONDS` 120→300 | April 28 confirmed 10/2-min still exceeded Synthflow HTTP step capacity; 38 contacts had dropped webhooks | 4 calls per 5-min slot. Manually ran redistribution SQL to spread 725 pending jobs. |
| 2026-04-29 | Added `_slot_aware_run_at()` to voicemail retry scheduling | VM retry callbacks used `now + delay_minutes` directly, recreating bursts at +24h/+48h | Fixed — retries now round to nearest slot boundary and compete for the same slot counter via `_compute_window_run_at()` |
| 2026-04-29 | Added `rebalance_call_slots` self-rescheduling job (every 5 min) | Concurrent scheduling races could still produce 5–7 calls/slot; manual redistribution was the only fix | Auto-corrects overages within 5 min; no manual intervention needed |
| 2026-04-29 | Added within-slot 75-second stagger to `_compute_window_run_at()` and rebalancer SQL | 4 calls/slot cap was correct but all 4 shared the same `run_at` second; confirmed drops at `04:00:21–04:00:23` (4 contacts, same second). Synthflow HTTP step concurrency limit applies per-second, not per-slot. | Calls in a slot now fire at +0s, +75s, +150s, +225s. Ran manual redistribution SQL on 722 pending jobs. Rebalancer SQL updated to apply same stagger automatically. |
| 2026-04-29 | Deployed PgBouncer connection pooler | 6 app services × up to 15 connections = ~90 potential connections against max_connections=100; exhaustion observed | Postgres sees max 20 connections regardless of replica count. Effective ceiling: unlimited scale at current volume. |
