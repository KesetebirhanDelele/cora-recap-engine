"""
Dashboard service — operator action orchestration.

All operator actions:
  1. Load and validate the target exception
  2. Execute state transitions with version-based conflict detection
  3. Write an AuditLog row (append-only; every action is recorded)
  4. Return a structured result dict

Conflict handling (from autonomous execution contract §5.5):
  - First successful state transition wins
  - Second attempt returns {"conflict": True} — no-op, no data lost
  - All actions are audit-logged regardless of outcome

Authorization is enforced at the route layer (app/api/deps.py).
This service does not check auth — callers must gate access.

force_finalize semantics:
  - Cancels all pending/claimed scheduled_jobs for the entity
  - Advances lead_state to tier 3 if entity is a lead
  - Executes GHL finalization write (AI Campaign = No, shadow-gated)
  - Resolves the exception with reason 'force_finalized'
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.exception import ExceptionRecord
from app.models.scheduled_job import ScheduledJob

logger = logging.getLogger(__name__)


# ── Audit helper ──────────────────────────────────────────────────────────────

def _audit(
    session: Session,
    entity_type: str,
    entity_id: str,
    action: str,
    operator_id: str,
    context: dict | None = None,
) -> AuditLog:
    """Append an immutable audit log entry."""
    entry = AuditLog(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        operator_id=operator_id,
        context_json=context or {},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(entry)
    session.flush()
    logger.info(
        "audit | action=%s entity=%s/%s operator=%s",
        action, entity_type, entity_id, operator_id,
    )
    return entry


def _load_open_exception(session: Session, exception_id: str) -> ExceptionRecord | None:
    exc = session.get(ExceptionRecord, exception_id)
    if exc is None or exc.status != "open":
        return None
    return exc


# ── Operator actions ──────────────────────────────────────────────────────────

def retry_now(
    session: Session,
    exception_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """
    Re-enqueue a new job for the entity immediately.

    Creates a new scheduled_job with run_at=now using the same job_type and
    entity from the exception context. Does NOT modify the exception status —
    the exception remains open until the retry succeeds or fails again.

    Returns: {"success": True, "new_job_id": "..."} or {"conflict": True}.
    """
    exc = _load_open_exception(session, exception_id)
    if exc is None:
        return {"conflict": True, "reason": "exception not open or not found"}

    entity_type = exc.entity_type or "call"
    entity_id = exc.entity_id or ""
    ctx = exc.context_json or {}
    job_type = ctx.get("job_type", "process_call_event")

    from app.worker.scheduler import schedule_job

    new_job = schedule_job(
        session=session,
        job_type=job_type,
        entity_type=entity_type,
        entity_id=entity_id,
        run_at=datetime.now(tz=timezone.utc),
        payload=ctx,
    )

    _audit(
        session, "exception", exception_id, "retry_now", operator_id,
        {"new_job_id": new_job.id, "job_type": job_type, "entity_id": entity_id},
    )

    logger.info(
        "retry_now | exception_id=%s new_job_id=%s operator=%s",
        exception_id, new_job.id, operator_id,
    )
    return {"success": True, "new_job_id": new_job.id}


def retry_with_delay(
    session: Session,
    exception_id: str,
    operator_id: str,
    delay_minutes: int,
) -> dict[str, Any]:
    """
    Schedule a delayed retry for the entity.

    Same as retry_now but run_at = now + delay_minutes.
    """
    if delay_minutes < 0:
        return {"error": "delay_minutes must be >= 0"}

    exc = _load_open_exception(session, exception_id)
    if exc is None:
        return {"conflict": True, "reason": "exception not open or not found"}

    entity_type = exc.entity_type or "call"
    entity_id = exc.entity_id or ""
    ctx = exc.context_json or {}
    job_type = ctx.get("job_type", "process_call_event")
    run_at = datetime.now(tz=timezone.utc) + timedelta(minutes=delay_minutes)

    from app.worker.scheduler import schedule_job

    new_job = schedule_job(
        session=session,
        job_type=job_type,
        entity_type=entity_type,
        entity_id=entity_id,
        run_at=run_at,
        payload=ctx,
    )

    _audit(
        session, "exception", exception_id, "retry_delay", operator_id,
        {"new_job_id": new_job.id, "delay_minutes": delay_minutes, "entity_id": entity_id},
    )

    return {"success": True, "new_job_id": new_job.id, "run_at": run_at.isoformat()}


def cancel_future_jobs(
    session: Session,
    exception_id: str,
    operator_id: str,
) -> dict[str, Any]:
    """
    Cancel all pending/claimed scheduled_jobs for the entity linked to this exception.

    Jobs in terminal states (completed, failed, cancelled) are not affected.
    Returns count of cancelled jobs.
    """
    exc = _load_open_exception(session, exception_id)
    if exc is None:
        return {"conflict": True, "reason": "exception not open or not found"}

    entity_type = exc.entity_type
    entity_id = exc.entity_id
    if not entity_id:
        return {"error": "exception has no entity_id — cannot cancel jobs"}

    pending_jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_type == entity_type,
            ScheduledJob.entity_id == entity_id,
            ScheduledJob.status.in_(["pending", "claimed"]),
        )
    ).all()

    from app.worker.claim import cancel_job as _cancel_job

    cancelled_ids: list[str] = []
    for job in pending_jobs:
        if _cancel_job(session, job.id):
            cancelled_ids.append(job.id)

    _audit(
        session, "exception", exception_id, "cancel_future_jobs", operator_id,
        {"entity_id": entity_id, "cancelled_job_ids": cancelled_ids,
         "count": len(cancelled_ids)},
    )

    logger.info(
        "cancel_future_jobs | exception_id=%s cancelled=%d operator=%s",
        exception_id, len(cancelled_ids), operator_id,
    )
    return {"success": True, "cancelled_count": len(cancelled_ids), "cancelled_ids": cancelled_ids}


def force_finalize(
    session: Session,
    exception_id: str,
    operator_id: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    """
    Force a workflow to terminal state.

    1. Cancel all future scheduled_jobs for the entity
    2. If entity is a lead: advance tier to 3, execute GHL finalization (shadow-gated)
    3. Resolve the exception with reason 'force_finalized'
    4. Audit log the action

    Version conflict on the exception → returns {"conflict": True}.
    """
    from app.config import get_settings

    settings = settings or get_settings()
    exc = _load_open_exception(session, exception_id)
    if exc is None:
        return {"conflict": True, "reason": "exception not open or not found"}

    entity_type = exc.entity_type or ""
    entity_id = exc.entity_id or ""

    # Step 1: cancel future jobs
    _cancel_result = cancel_future_jobs(session, exception_id, operator_id)
    cancelled_count = _cancel_result.get("cancelled_count", 0) if isinstance(_cancel_result, dict) else 0

    # Step 2: lead finalization
    finalized_lead = False
    if entity_type == "lead" and entity_id:
        finalized_lead = _finalize_lead(session, entity_id, settings)

    # Step 3: resolve exception with version guard
    from app.worker.exceptions import resolve_exception

    resolved = resolve_exception(
        session, exception_id,
        resolved_by=operator_id,
        reason="force_finalized",
    )
    if not resolved:
        return {"conflict": True, "reason": "exception status changed concurrently"}

    _audit(
        session, "exception", exception_id, "force_finalize", operator_id,
        {
            "entity_id": entity_id,
            "entity_type": entity_type,
            "cancelled_jobs": cancelled_count,
            "lead_finalized": finalized_lead,
        },
    )

    return {
        "success": True,
        "cancelled_jobs": cancelled_count,
        "lead_finalized": finalized_lead,
    }


def _finalize_lead(session: Session, contact_id: str, settings: Any) -> bool:
    """
    Advance lead tier to 3 and execute GHL finalization write (shadow-gated).
    Returns True if the lead was found and finalized.
    """
    from sqlalchemy import update

    from app.models.lead_state import LeadState

    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()

    if lead is None:
        logger.warning("force_finalize: lead_state not found | contact_id=%s", contact_id)
        return False

    if lead.ai_campaign_value == "3":
        logger.info("force_finalize: lead already at tier 3 | contact_id=%s", contact_id)
        return True

    now = datetime.now(tz=timezone.utc)
    result: CursorResult = session.execute(  # type: ignore[assignment]
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(ai_campaign_value="3", version=lead.version + 1, updated_at=now)
    )
    session.flush()
    if result.rowcount == 0:
        logger.warning("force_finalize: version conflict on lead | contact_id=%s", contact_id)
        return False

    # GHL finalization write (shadow-gated)
    from app.adapters.ghl import GHLClient

    ghl = GHLClient(settings=settings)
    ai_field = settings.ghl_field_ai_campaign or "AI Campaign"
    ghl.update_contact_fields(contact_id=contact_id, field_updates={ai_field: "No"})
    logger.info("force_finalize: lead finalized | contact_id=%s shadow=%s",
                contact_id, not settings.ghl_writes_enabled)
    return True


# ── Query functions ───────────────────────────────────────────────────────────

def list_exceptions(
    session: Session,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """
    List exceptions with optional filtering and pagination.

    search: matched against entity_id, call_event_id (prefix match).
    Returns {"exceptions": [...], "total": N, "limit": L, "offset": O}.
    """
    from sqlalchemy import func as sqlfunc

    q = select(ExceptionRecord)

    if status:
        q = q.where(ExceptionRecord.status == status)
    if severity:
        q = q.where(ExceptionRecord.severity == severity)
    if search:
        q = q.where(
            (ExceptionRecord.entity_id.contains(search))
            | (ExceptionRecord.call_event_id.contains(search))
        )

    total = session.scalar(select(sqlfunc.count()).select_from(q.subquery()))
    rows = session.scalars(
        q.order_by(ExceptionRecord.created_at.desc()).limit(limit).offset(offset)
    ).all()

    return {
        "exceptions": [_exc_to_dict(e) for e in rows],
        "total": total or 0,
        "limit": limit,
        "offset": offset,
    }


def get_exception_detail(session: Session, exception_id: str) -> dict | None:
    exc = session.get(ExceptionRecord, exception_id)
    if exc is None:
        return None

    # Include audit trail for this exception
    audit_rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "exception", AuditLog.entity_id == exception_id)
        .order_by(AuditLog.created_at.asc())
    ).all()

    result = _exc_to_dict(exc)
    result["audit_trail"] = [
        {
            "id": a.id,
            "action": a.action,
            "operator_id": a.operator_id,
            "context": a.context_json,
            "created_at": a.created_at.isoformat(),
        }
        for a in audit_rows
    ]
    return result


def _exc_to_dict(exc: ExceptionRecord) -> dict:
    return {
        "id": exc.id,
        "type": exc.type,
        "severity": exc.severity,
        "status": exc.status,
        "entity_type": exc.entity_type,
        "entity_id": exc.entity_id,
        "call_event_id": exc.call_event_id,
        "resolution_reason": exc.resolution_reason,
        "resolved_by": exc.resolved_by,
        "context_json": exc.context_json,
        "version": exc.version,
        "created_at": exc.created_at.isoformat(),
        "updated_at": exc.updated_at.isoformat(),
    }
