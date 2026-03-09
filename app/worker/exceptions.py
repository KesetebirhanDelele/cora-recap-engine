"""
Exception record lifecycle service.

Creates and manages ExceptionRecord rows for surfaced failures.

Operator actions (dashboard Phase 8) also use these functions to
transition exception status. All transitions use optimistic concurrency
(version check) so concurrent operator actions fail cleanly.

Severity guide:
  critical — processing cannot continue; operator action required
  warning  — non-blocking; process continued with degraded behavior

Type guide (common values):
  identity_resolution  — call_id or contact unresolvable
  ghl_auth             — GHL API key invalid or expired
  ghl_write_failed     — GHL field/task/note write failed after retries
  openai_failed        — AI call failed after retries
  tier_invalid         — voicemail tier transition violates state machine
  retry_budget_exhausted — job exhausted all retry attempts
  postgres_write_failed  — DB write failure on required state
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.exception import ExceptionRecord

logger = logging.getLogger(__name__)


def create_exception(
    session: Session,
    type: str,
    context: dict,
    severity: str = "critical",
    call_event_id: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> ExceptionRecord:
    """
    Create and persist a new ExceptionRecord with status='open'.

    call_event_id is nullable — some exceptions arise before a call event
    record exists (e.g., GHL auth failure at worker startup).
    """
    now = datetime.now(tz=timezone.utc)
    exc_record = ExceptionRecord(
        id=str(uuid.uuid4()),
        call_event_id=call_event_id,
        entity_type=entity_type,
        entity_id=entity_id,
        type=type,
        severity=severity,
        status="open",
        context_json=context,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(exc_record)
    session.flush()
    logger.error(
        "exception created | id=%s type=%s severity=%s entity=%s/%s",
        exc_record.id, type, severity, entity_type, entity_id,
    )
    return exc_record


def resolve_exception(
    session: Session,
    exception_id: str,
    resolved_by: str,
    reason: str,
) -> bool:
    """
    Resolve an open exception.

    Returns True if the transition succeeded, False on version conflict
    (another operator already acted on it).
    """
    exc = session.get(ExceptionRecord, exception_id)
    if exc is None or exc.status != "open":
        return False

    now = datetime.now(tz=timezone.utc)
    result = session.execute(
        update(ExceptionRecord)
        .where(
            ExceptionRecord.id == exception_id,
            ExceptionRecord.status == "open",
            ExceptionRecord.version == exc.version,
        )
        .values(
            status="resolved",
            resolved_by=resolved_by,
            resolution_reason=reason,
            version=exc.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    success = result.rowcount > 0
    if success:
        logger.info(
            "exception resolved | id=%s by=%s reason=%r",
            exception_id, resolved_by, reason,
        )
    else:
        logger.warning(
            "exception resolve: version conflict | id=%s", exception_id
        )
    return success


def ignore_exception(
    session: Session,
    exception_id: str,
    resolved_by: str,
    reason: str,
) -> bool:
    """
    Mark an open exception as ignored with a reason code.

    Returns True on success, False on version conflict.
    """
    exc = session.get(ExceptionRecord, exception_id)
    if exc is None or exc.status != "open":
        return False

    now = datetime.now(tz=timezone.utc)
    result = session.execute(
        update(ExceptionRecord)
        .where(
            ExceptionRecord.id == exception_id,
            ExceptionRecord.status == "open",
            ExceptionRecord.version == exc.version,
        )
        .values(
            status="ignored",
            resolved_by=resolved_by,
            resolution_reason=reason,
            version=exc.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    return result.rowcount > 0
