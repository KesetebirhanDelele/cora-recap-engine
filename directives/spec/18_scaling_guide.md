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
**Signal:** Postgres `max_connections` near limit; API or worker logs show connection wait time.

**Action:** Add PgBouncer in transaction mode in front of Postgres. No application code changes — only `DATABASE_URL` environment variable is updated to point to PgBouncer.

```yaml
# docker-compose.yml addition
pgbouncer:
  image: pgbouncer/pgbouncer:latest
  environment:
    DATABASES_HOST: postgres
    DATABASES_PORT: 5432
    DATABASES_DBNAME: cora
    PGBOUNCER_POOL_MODE: transaction
    PGBOUNCER_MAX_CLIENT_CONN: 500
    PGBOUNCER_DEFAULT_POOL_SIZE: 20
```

Update `DATABASE_URL` in `.env`:
```
DATABASE_URL=postgresql+psycopg2://postgres:<password>@pgbouncer:5432/cora
```

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
| Synthflow | Per-account call concurrency cap | Check Synthflow account plan; cap worker concurrency for Synthflow-bound job types |
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
