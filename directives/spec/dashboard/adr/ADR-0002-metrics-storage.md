# ADR-0002 — Metrics storage strategy

**Status**: Accepted
**Date**: 2026-04-05
**Deciders**: Engineering

---

## Context

The dashboard needs to display time-series system metrics (queue lag, error rate, worker health) and business KPIs (pickup rate, voicemail rate). These values must be available:

1. As current snapshots (health tiles)
2. As historical trends (sparklines, 7-day trend charts)

The constraint from `spec/dashboard/03_constraints.md` is explicit:
> Postgres is the only source of truth. No external observability tools (no Prometheus, no Grafana).

This means a time-series database (InfluxDB, TimescaleDB) is out of scope. All metrics must be stored in Postgres.

---

## Decision

**Pre-aggregated snapshots in a `system_metrics` table** (one row per metric per collection cycle) with a **60-second collection interval**, combined with **on-demand derivation from source tables** for the live health endpoint.

The two-level approach:
- `GET /dashboard/health` — derives values directly from `scheduled_jobs` and `exceptions` at query time (fresh, low latency)
- `GET /dashboard/metrics` (trends) — reads from `system_metrics` for historical sparklines; computes current KPIs on demand from `call_events` and `lead_state`

---

## Options considered

### Option A: Query source tables on every request (rejected)
Derive all metrics from `scheduled_jobs`, `call_events`, `lead_state`, etc. at API request time. No pre-aggregation.

**Pros**: No new tables. Always fresh.
**Cons**: Under load (1000+ leads, 10 000+ call_events), aggregate queries will be slow. Every dashboard page refresh triggers full table scans. Does not support historical trends without expensive window queries.
**Rejected because**: Fails EVAL-PERF-01 and EVAL-PERF-03 (< 1s p95) at scale.

### Option B: `system_metrics` table + collector job (selected)
Pre-aggregate metrics every 60 seconds into a `system_metrics` table. Health endpoint reads pre-computed values for trends; uses direct queries for the current snapshot only.

**Pros**: Historical trends are cheap to query (indexed `(metric_name, recorded_at DESC)`). Source table queries are bounded (only current state, not history). Collector failures degrade gracefully (last known value still queryable).
**Cons**: 60-second staleness for trend data. Requires a new background job and table.
**Selected because**: Meets performance requirements. Bounded write volume (one row per metric per minute ≈ 15 metrics × 1440 min/day = 21 600 rows/day, negligible at Postgres scale). Aligns with existing self-rescheduling job pattern.

### Option C: TimescaleDB / InfluxDB (rejected)
Use a purpose-built time-series database.

**Pros**: Optimal for time-series. Built-in aggregation and retention policies.
**Cons**: Violates the "no new infrastructure dependencies" constraint. Requires Docker Compose additions. Adds operational complexity.
**Rejected because**: Explicitly out of scope per constraints.

### Option D: Redis for metrics caching (rejected)
Store computed metrics in Redis hashes with TTL.

**Pros**: Fast reads. No Postgres load for metric reads.
**Cons**: Not authoritative. Lost on Redis restart. Violates "Postgres is the only source of truth" constraint.
**Rejected because**: Constraint violation.

---

## Consequences

### Accepted
- `system_metrics` rows accumulate at ~21 600/day. Rows older than 30 days are purged on a weekly cleanup cycle. Storage is negligible.
- 60-second staleness for trend data is acceptable for an operational dashboard; it is not a trading system.
- Direct query for the health endpoint means `GET /dashboard/health` is slightly slower than reading pre-computed values, but bounded to simple `scheduled_jobs` and `exceptions` queries that have existing indexes.

### Risk
- If the `collect_metrics_job` scheduler stops running (worker restart, bug), trend data will have gaps. The health endpoint remains accurate since it derives values directly. Gaps in trend sparklines are acceptable and visible to operators.
- Mitigation: the metrics collector logs each cycle; missing cycles are detectable from log analysis.

---

## Retention policy

| Table | Retention | Cleanup |
|---|---|---|
| `system_metrics` | 30 days | Weekly cleanup via `collect_metrics_job` |
| `event_stream` | 7 days | Weekly cleanup via `collect_metrics_job` |
| `alert_events` (resolved) | 90 days | Weekly cleanup via `collect_metrics_job` |

Long-term KPI reporting (enrollment rates by month, etc.) is always derived from `call_events` and `lead_state`, which have indefinite retention per the main system spec.
