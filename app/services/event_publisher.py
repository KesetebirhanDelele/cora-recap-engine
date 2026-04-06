"""
Event publisher — writes system events to event_stream table and Redis Pub/Sub.

publish_event() is the single call site for all dashboard real-time events.
It is non-fatal: if Redis is down or the DB write fails, the parent job
continues. Events are best-effort for real-time; the event_stream table
provides the durable fallback for polling.

Call sites:
  - app/worker/claim.py        (job_started, job_completed, job_failed)
  - app/worker/exceptions.py   (exception_created)
  - app/worker/jobs/call_processing.py (call_processed)
  - app/core/campaigns.py      (campaign_switched)

Redis channel: dashboard:events
Event payload must contain no PII beyond contact_id and no credential fields.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "dashboard:events"


def publish_event(
    session,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    contact_id: str | None,
    message: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """
    Write a system event to event_stream and publish to Redis Pub/Sub.

    Non-fatal: exceptions are logged but never raised to the caller.
    The parent job must not fail because of an event publication failure.

    Parameters
    ----------
    session     : SQLAlchemy sync Session (already open; caller manages commit)
    event_type  : One of: job_started | job_completed | job_failed |
                  exception_created | call_processed | campaign_switched |
                  alert_triggered
    entity_type : "call" | "lead" | "exception" | "scheduled_job" | None
    entity_id   : Primary identifier for the entity (call_id, contact_id, etc.)
    contact_id  : GHL contact identifier for per-lead feed filtering
    message     : Human-readable one-line summary (no PII, no credentials)
    payload     : Optional dict; must be < 4 KB; no transcript or credential fields
    """
    from app.models.event_stream import EventStream

    now = datetime.now(tz=timezone.utc)
    event_id = str(uuid.uuid4())
    event_payload = payload or {}

    # 1. Write to event_stream (durable fallback)
    # Use a nested transaction (savepoint) so that if the write fails, the outer
    # session transaction is not contaminated — the parent job must not fail because
    # of an event publication failure.
    try:
        with session.begin_nested():
            row = EventStream(
                id=event_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                contact_id=contact_id,
                message=message,
                payload=event_payload,
                created_at=now,
            )
            session.add(row)
    except Exception as exc:
        logger.error(
            "event_publisher: failed to write event_stream | event_type=%s entity_id=%s: %s",
            event_type, entity_id, exc,
        )
        return  # skip Redis publish if DB write failed

    # 2. Publish to Redis Pub/Sub (real-time delivery)
    event_json = json.dumps({
        "id": event_id,
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "contact_id": contact_id,
        "message": message,
        "payload": event_payload,
        "created_at": now.isoformat(),
    })
    try:
        _publish_to_redis(event_json)
    except Exception as exc:
        logger.warning(
            "event_publisher: Redis publish failed (non-fatal) | event_type=%s: %s",
            event_type, exc,
        )


def _publish_to_redis(message: str) -> None:
    """Publish a message to the Redis dashboard channel using sync client."""
    import redis as redis_lib

    from app.config import get_settings

    settings = get_settings()
    url = (
        settings.redis_url
        or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
    )
    client = redis_lib.from_url(url)
    client.publish(REDIS_CHANNEL, message)
    client.close()
