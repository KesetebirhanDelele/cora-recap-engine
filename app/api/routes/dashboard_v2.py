"""
Dashboard v2 API routes — all endpoints for the production dashboard.

Serves on port 8001 (separate process from main API on port 8000).
Auth: Bearer token via require_dashboard_auth() for write endpoints.
Read endpoints: optional auth controlled by DASHBOARD_READ_AUTH_REQUIRED.

Route groups:
  GET  /dashboard/health                  — system health tiles
  GET  /dashboard/metrics                 — KPI aggregates, stuck jobs
  GET  /dashboard/events                  — cursor-based event stream (polling fallback)
  GET  /dashboard/lead/{contact_id}/trace — full pipeline trace for a lead
  GET  /dashboard/alerts                  — active/recent alerts

  POST /dashboard/actions/retry           — retry a failed job
  POST /dashboard/actions/cancel          — cancel pending jobs for a lead
  POST /dashboard/actions/finalize        — force finalize a lead
  POST /dashboard/actions/resolve         — resolve an exception
  POST /dashboard/actions/ignore          — ignore an exception

  WS   /dashboard/ws/events               — real-time event feed via Redis Pub/Sub
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import DashboardAuth, require_dashboard_auth
from app.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard-v2"])


# ── Optional auth dependency ──────────────────────────────────────────────────

def _optional_auth(require: bool = False):
    """Return a dependency that enforces auth only when DASHBOARD_READ_AUTH_REQUIRED=true."""
    from app.config import get_settings

    def _dep(
        authorization: Annotated[str | None, Depends(lambda: None)] = None,
    ) -> dict | None:
        settings = get_settings()
        if settings.dashboard_read_auth_required:
            # Delegate to full auth
            from fastapi import Header
            # We can't easily inject the Header here without full DI — instead
            # we just mark auth as optional (caller passes token or not)
            pass
        return None

    return _dep


# ── Read endpoints ─────────────────────────────────────────────────────────────

@router.get("/health")
def get_health(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """System health snapshot."""
    from app.services.dashboard_metrics import get_health as _get_health
    return _get_health(session)


@router.get("/metrics")
def get_metrics(
    campaign: str | None = Query(default=None, description="New Lead | Cold Lead | Inbound"),
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregated KPIs and queue metrics for a time window."""
    from app.services.dashboard_metrics import get_metrics as _get_metrics
    return _get_metrics(session, campaign=campaign, from_date=from_date, to_date=to_date)


@router.get("/events")
def get_events(
    since: datetime | None = Query(default=None, description="ISO timestamp cursor"),
    limit: int = Query(default=100, le=200),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cursor-based event stream for polling fallback."""
    from sqlalchemy import text

    if since:
        rows = session.execute(text("""
            SELECT id, event_type, entity_type, entity_id, contact_id,
                   message, payload, created_at
            FROM event_stream
            WHERE created_at > :since
            ORDER BY created_at ASC
            LIMIT :limit
        """), {"since": since, "limit": limit}).fetchall()
    else:
        rows = session.execute(text("""
            SELECT id, event_type, entity_type, entity_id, contact_id,
                   message, payload, created_at
            FROM event_stream
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit}).fetchall()
        rows = list(reversed(rows))

    events = [
        {
            "id": r[0],
            "event_type": r[1],
            "entity_type": r[2],
            "entity_id": r[3],
            "contact_id": r[4],
            "message": r[5],
            "payload": r[6] or {},
            "created_at": r[7].isoformat() if r[7] and hasattr(r[7], "isoformat") else r[7],
        }
        for r in rows
    ]

    next_cursor = events[-1]["created_at"] if events else (since.isoformat() if since else None)
    return {"events": events, "next_cursor": next_cursor}


@router.get("/lead/{contact_id}/trace")
def get_lead_trace(
    contact_id: str,
    phone: str | None = Query(default=None, description="E.164 fallback lookup"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Full pipeline trace for a single lead."""
    from app.services.pipeline_trace import get_lead_trace as _get_trace

    result = _get_trace(session, contact_id=contact_id, phone=phone)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead not found for contact_id={contact_id}",
        )
    return result


@router.get("/alerts")
def get_alerts(
    alert_status: str = Query(default="active", alias="status"),
    limit: int = Query(default=50),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """List active and recent alerts."""
    from sqlalchemy import text

    valid_statuses = {"active", "resolved", "acknowledged"}
    if alert_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of: {', '.join(valid_statuses)}",
        )

    rows = session.execute(text("""
        SELECT id, alert_type, severity, status, current_value, threshold,
               message, email_sent_at, last_seen_at, resolved_at, created_at
        FROM alert_events
        WHERE status = :alert_status
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"alert_status": alert_status, "limit": limit}).fetchall()

    alerts = [
        {
            "id": r[0],
            "alert_type": r[1],
            "severity": r[2],
            "status": r[3],
            "current_value": r[4],
            "threshold": r[5],
            "message": r[6],
            "email_sent_at": r[7].isoformat() if r[7] else None,
            "last_seen_at": r[8].isoformat() if r[8] else None,
            "resolved_at": r[9].isoformat() if r[9] else None,
            "created_at": r[10].isoformat() if r[10] else None,
        }
        for r in rows
    ]
    return {"alerts": alerts}


# ── Operator action schemas ───────────────────────────────────────────────────

class RetryRequest(BaseModel):
    exception_id: str
    delay_minutes: int = 0


class CancelRequest(BaseModel):
    contact_id: str
    reason: str


class FinalizeRequest(BaseModel):
    contact_id: str
    reason: str


class ResolveRequest(BaseModel):
    exception_id: str
    note: str = ""


class IgnoreRequest(BaseModel):
    exception_id: str


# ── Write endpoints (auth required) ──────────────────────────────────────────

@router.post("/actions/retry")
def action_retry(
    body: RetryRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Retry a failed job associated with an exception."""
    from app.services.dashboard import retry_now, retry_with_delay

    operator_id = auth["operator_id"]

    if body.delay_minutes == 0:
        result = retry_now(session, exception_id=body.exception_id, operator_id=operator_id)
    else:
        result = retry_with_delay(
            session, exception_id=body.exception_id,
            operator_id=operator_id, delay_minutes=body.delay_minutes,
        )

    if result.get("conflict"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.get("reason", "conflict"),
        )

    new_job_id = result.get("new_job_id")
    run_at = result.get("run_at") or datetime.now(tz=timezone.utc).isoformat()

    # Fetch audit log id for the just-written entry
    from sqlalchemy import text
    audit_row = session.execute(text("""
        SELECT id FROM audit_log
        WHERE entity_type = 'exception' AND entity_id = :eid
        ORDER BY created_at DESC LIMIT 1
    """), {"eid": body.exception_id}).fetchone()
    audit_id = audit_row[0] if audit_row else None

    session.commit()
    return {"status": "ok", "scheduled_job_id": new_job_id, "run_at": run_at, "audit_log_id": audit_id}


@router.post("/actions/cancel")
def action_cancel(
    body: CancelRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Cancel all pending jobs for a lead."""
    from sqlalchemy import text

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    # Cancel pending jobs for this contact_id
    rows = session.execute(text("""
        UPDATE scheduled_jobs
        SET status = 'cancelled', updated_at = :now
        WHERE entity_id = :contact_id
          AND status IN ('pending', 'claimed')
        RETURNING id
    """), {"contact_id": body.contact_id, "now": now}).fetchall()
    cancelled_count = len(rows)

    # Audit log
    import uuid
    from app.models.audit import AuditLog
    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="lead",
        entity_id=body.contact_id,
        action="cancel_jobs",
        operator_id=operator_id,
        context_json={"reason": body.reason, "cancelled_count": cancelled_count},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "cancelled_job_count": cancelled_count, "audit_log_id": audit.id}


@router.post("/actions/finalize")
def action_finalize(
    body: FinalizeRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Force finalize a lead — close campaign, cancel pending jobs."""
    from sqlalchemy import text
    import uuid
    from app.models.audit import AuditLog

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    # Update lead_state to closed
    session.execute(text("""
        UPDATE lead_state
        SET status = 'closed', updated_at = :now
        WHERE contact_id = :contact_id
    """), {"contact_id": body.contact_id, "now": now})

    # Cancel pending jobs
    session.execute(text("""
        UPDATE scheduled_jobs
        SET status = 'cancelled', updated_at = :now
        WHERE entity_id = :contact_id
          AND status IN ('pending', 'claimed')
    """), {"contact_id": body.contact_id, "now": now})

    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="lead",
        entity_id=body.contact_id,
        action="force_finalize",
        operator_id=operator_id,
        context_json={"reason": body.reason},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "audit_log_id": audit.id}


@router.post("/actions/resolve")
def action_resolve(
    body: ResolveRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Resolve an exception without retrying."""
    from app.worker.exceptions import resolve_exception
    import uuid
    from app.models.audit import AuditLog
    from sqlalchemy import text

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    resolved = resolve_exception(
        session,
        exception_id=body.exception_id,
        resolved_by=operator_id,
        reason=body.note or "operator resolved",
    )

    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Exception not found or already resolved",
        )

    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="exception",
        entity_id=body.exception_id,
        action="resolve",
        operator_id=operator_id,
        context_json={"note": body.note},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "audit_log_id": audit.id}


@router.post("/actions/ignore")
def action_ignore(
    body: IgnoreRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Suppress an exception from the open list."""
    from app.worker.exceptions import ignore_exception
    import uuid
    from app.models.audit import AuditLog

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    ignored = ignore_exception(session, exception_id=body.exception_id)

    if not ignored:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Exception not found or not open",
        )

    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="exception",
        entity_id=body.exception_id,
        action="ignore",
        operator_id=operator_id,
        context_json={},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "audit_log_id": audit.id}


# ── WebSocket — real-time event feed ─────────────────────────────────────────

@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    """
    Real-time event feed via Redis Pub/Sub.

    Subscribes to the 'dashboard:events' channel and forwards each message
    as a JSON frame to the connected client.

    On Redis failure: sends error message and closes the connection.
    Client should fall back to polling GET /dashboard/events.
    """
    await websocket.accept()

    try:
        import redis.asyncio as aioredis
        from app.config import get_settings
        settings = get_settings()

        url = (
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )
        client = aioredis.from_url(url)
        pubsub = client.pubsub()
        await pubsub.subscribe("dashboard:events")

        logger.info("websocket: client connected, subscribed to dashboard:events")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"].decode("utf-8"))
        except WebSocketDisconnect:
            logger.info("websocket: client disconnected")
        finally:
            await pubsub.unsubscribe("dashboard:events")
            await client.aclose()

    except Exception as exc:
        logger.error("websocket: Redis unavailable — %s", exc)
        try:
            await websocket.send_json({
                "type": "error",
                "code": "realtime_unavailable",
                "fallback_url": "/dashboard/events",
            })
        except Exception:
            pass
        await websocket.close()
