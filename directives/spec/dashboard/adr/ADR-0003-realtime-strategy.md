# ADR-0003 — Real-time delivery strategy

**Status**: Accepted
**Date**: 2026-04-05
**Deciders**: Engineering

---

## Context

The dashboard requires a live activity feed showing system events (job completions, exceptions, call processing) within 2 seconds of occurrence. The following constraints apply:

- No external services (no Firebase, no Pusher, no Ably, no paid WebSocket services)
- Postgres is authoritative — events must not be lost if Redis is unavailable
- The system already uses Redis (via RQ) — it is available as an infrastructure primitive
- The existing API uses FastAPI, which supports WebSocket natively

---

## Decision

**Primary**: Redis Pub/Sub → FastAPI WebSocket → browser client

**Fallback**: `event_stream` Postgres table → `GET /dashboard/events` polling (5-second interval) → browser client

Both paths are always active:
- Every event is written to `event_stream` table (authoritative, durable)
- Every event is published to Redis channel `dashboard:events` (fast, ephemeral)
- The WebSocket gateway subscribes to Redis and forwards to connected clients
- If the WebSocket connection fails or Redis is unavailable, the frontend detects the close event and switches to polling

---

## Options considered

### Option A: Redis Pub/Sub + WebSocket (primary path, selected)
Worker publishes events to Redis channel on state transitions. FastAPI WebSocket gateway subscribes and forwards to clients.

**Pros**: Sub-second delivery. No polling overhead. No DB queries in the hot path.
**Cons**: Events are lost if Redis restarts (ephemeral). Requires `aioredis` or `redis.asyncio` in the dependency set.
**Selected as primary because**: Meets the 2-second latency requirement. Redis is already a required dependency.

### Option B: Server-Sent Events (SSE) over HTTP (rejected as primary)
FastAPI SSE endpoint holds a long-lived HTTP connection and pushes events.

**Pros**: Simpler than WebSocket (HTTP/1.1 compatible). Automatic reconnection in browser.
**Cons**: SSE requires holding a DB or Redis subscription per connected client. Under concurrent operator sessions, this creates N persistent connections on the API process. More complex to scale than WebSocket.
**Decision**: Not selected as primary. Could replace WebSocket in future if WebSocket proxy complexity becomes a problem (see OQ-05).

### Option C: Short-interval polling only (fallback path, kept as fallback)
Frontend polls `GET /dashboard/events?since=<cursor>` every N seconds.

**Pros**: Simple. No persistent connections. Works through any proxy without configuration.
**Cons**: Minimum latency is N seconds. Adds Postgres query load every N seconds per active client.
**Selected as fallback**: Polling at 5-second intervals provides acceptable degraded experience when Redis is down. The `event_stream` table ensures events are always available to poll.

### Option D: PostgreSQL LISTEN/NOTIFY (rejected)
Postgres triggers publish NOTIFY on state transitions. FastAPI listens via `LISTEN`.

**Pros**: No Redis dependency for real-time. Postgres-native.
**Cons**: Requires `asyncpg` (async Postgres driver) which is not currently in the dependency stack (workers use `psycopg2` sync). LISTEN connections are a separate persistent DB connection per listener. Notification payloads are capped at 8 KB. More complex than Redis Pub/Sub for this use case.
**Rejected because**: Adds a new async DB driver dependency. Connection management complexity. Redis is already available.

---

## Event schema

All events use this structure in both the Redis Pub/Sub payload and the `event_stream` table:

```json
{
  "id": "uuid-v4",
  "event_type": "job_completed",
  "entity_type": "call",
  "entity_id": "call-id",
  "contact_id": "contact-id-or-null",
  "message": "Human-readable one-line summary",
  "payload": {},
  "created_at": "2026-04-05T14:00:00Z"
}
```

**`payload` rules**:
- No PII beyond `contact_id` (already present at top level)
- No transcript content
- No credential fields
- Bounded size: payload must be < 4 KB to stay well within Redis pub/sub limits

---

## Fallback behavior

1. WebSocket connects → subscribes to Redis → forwards events.
2. Redis PUBLISH fails (connection error): `publish_event()` logs a warning; the `event_stream` write still completes. Parent job is unaffected.
3. WebSocket client receives `{"type": "error", "code": "realtime_unavailable"}` message and disconnects.
4. Frontend catches `onclose` event and switches to polling `GET /dashboard/events?since=<last_cursor>` every 5 seconds.
5. When Redis recovers: the WebSocket endpoint reconnects automatically on the next client request. Client switches back from polling by re-establishing the WebSocket connection.
6. During the Redis outage period: all events are durable in `event_stream` and available via polling. No events are lost.

---

## Consequences

### Accepted
- `redis>=4.5` with `redis.asyncio` must be confirmed in `pyproject.toml`. The deprecated `aioredis` package must NOT be used (OQ-04 resolved).
- The WebSocket endpoint must run in an async FastAPI context. The dashboard API must use `uvicorn` with `asyncio` (already the case).
- Redis is now required for the WebSocket path but not for dashboard correctness — the fallback keeps the dashboard functional without it.
- One WebSocket connection is held per connected browser tab. Under normal operational load (2–5 concurrent operators), this is negligible.

### Risk
- High-frequency events (e.g., 100+ jobs completing per second during a large batch) could flood the activity feed. Mitigation: the frontend cap of 100 events in the feed provides natural throttling on the display side. The `event_stream` table accumulates all events regardless.
