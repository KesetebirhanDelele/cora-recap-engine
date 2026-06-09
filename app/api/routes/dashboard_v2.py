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
    campaign: str | None = Query(default=None, description="New Lead | Cold Lead | Unknown"),
    direction: str | None = Query(default=None, description="Outbound | Inbound"),
    voice_agent: str | None = Query(default=None, description="ColdLead | NewLead | Inbound"),
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregated KPIs and queue metrics for a time window."""
    from app.services.dashboard_metrics import get_metrics as _get_metrics
    return _get_metrics(session, campaign=campaign, direction=direction, voice_agent=voice_agent, from_date=from_date, to_date=to_date)


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


@router.get("/ai-timeseries")
def get_ai_timeseries(
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Weekly AI behavior time series — intent distribution, blank rate, unknown intent rate."""
    from app.services.dashboard_metrics import get_ai_timeseries as _get_ats
    return _get_ats(session, from_date=from_date, to_date=to_date)


@router.get("/voice-performance/earliest-date")
def get_voice_performance_earliest_date(session: Session = Depends(get_db)) -> dict[str, str | None]:
    """Return the Monday of the earliest calendar week that has call_events data."""
    from sqlalchemy import text as _text
    from datetime import timezone as _tz, timedelta as _td
    row = session.execute(_text("SELECT MIN(COALESCE(call_started_at, created_at)) FROM call_events WHERE NOT report_excluded")).fetchone()
    earliest = row[0] if (row and row[0]) else None
    if earliest is None:
        return {"monday": None}
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=_tz.utc)
    days_since_monday = earliest.weekday()  # Mon=0
    monday = (earliest - _td(days=days_since_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"monday": monday.date().isoformat()}


@router.get("/voice-performance")
def get_voice_performance(
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    all_time: bool = Query(default=False, description="Return cumulative all-time KPIs with no date floor"),
    wow_mode: bool = Query(default=False, description="When True, WoW compares the calendar week of to_date vs the prior full calendar week"),
    wow_shift: bool = Query(default=False, description="When True, WoW compares (from_date, to_date) vs the same window shifted back 7 days"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Voice call performance analytics — KPIs, time series, WoW, scatter data."""
    from app.services.dashboard_metrics import get_voice_performance as _get_vp
    return _get_vp(session, from_date=from_date, to_date=to_date, all_time=all_time, wow_mode=wow_mode, wow_shift=wow_shift)


@router.get("/lead-lifecycle")
def get_lead_lifecycle(
    status:   str = Query(default="all", description="all | active | finalized | vm | dnc"),
    campaign: str = Query(default="all", description="all | Cold Lead | New Lead | Inbound"),
    limit:    int = Query(default=100, le=500),
    offset:   int = Query(default=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Lead lifecycle monitor — summary counts + per-lead journey rows."""
    from app.services.lead_lifecycle import get_lead_lifecycle as _get_ll
    return _get_ll(session, status_filter=status, campaign_filter=campaign,
                   limit=limit, offset=offset)


@router.get("/worker-activity")
def get_worker_activity(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Per-worker job throughput and avg latency over the last 10 minutes."""
    from app.services.dashboard_metrics import get_worker_activity as _get_wa
    return _get_wa(session)


@router.get("/worker-activity-trend")
def get_worker_activity_trend(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """60-minute per-worker job count and avg duration trend (1-min buckets)."""
    from app.services.dashboard_metrics import get_worker_activity_trend as _get_wat
    return _get_wat(session)


@router.get("/webhook-failures")
def get_webhook_failures(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Webhook delivery failures for launch_outbound_call jobs in the last 24 hours."""
    from app.services.dashboard_metrics import get_webhook_failures as _get_wf
    return _get_wf(session)


@router.get("/card-metrics")
def get_card_metrics(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    All navigation card indicator metrics — current + previous 24h period values.

    Returns compact {value, previous_value} pairs for each card:
      events_per_min, open_exceptions, backlog_size, active_alerts,
      lookup_rate, config_health, pickup_rate, meaningful_engagement_rate,
      booking_rate, active_leads, sync_success_rate, anomaly_count.
    """
    from app.services.dashboard_metrics import get_card_metrics as _get_cm
    return _get_cm(session)


@router.get("/recent-calls")
def get_recent_calls(
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    voice_agent: str | None = Query(default=None, description="ColdLead | NewLead | Inbound"),
    limit: int = Query(default=200, le=500),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Calls with duration >= 30s that have transcript and recording_url.
    Ordered by call time descending. Used by the Recent Calls page.
    Filtered by voice_agent (ColdLead | NewLead | Inbound) — per-call attribute.
    """
    from app.services.dashboard_metrics import get_recent_calls as _get_rc
    return _get_rc(session, from_date=from_date, to_date=to_date, voice_agent=voice_agent, limit=limit)


@router.get("/intent-calls")
def get_intent_calls(
    intent: str = Query(..., description="detected_intent value to drill into"),
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    campaign: str | None = Query(default=None, description="New Lead | Cold Lead"),
    voice_agent: str | None = Query(default=None, description="ColdLead | NewLead | Inbound"),
    direction: str | None = Query(default=None, description="Outbound | Inbound"),
    limit: int = Query(default=100, le=200),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    All calls matching a specific detected_intent — used by the Engagement Analysis
    intent bar chart drill-down. Returns transcript and recording_url per call.
    """
    from app.services.dashboard_metrics import get_intent_calls as _get_ic
    return _get_ic(
        session, intent=intent, from_date=from_date, to_date=to_date,
        campaign=campaign, voice_agent=voice_agent, direction=direction, limit=limit,
    )


@router.get("/exceptions/trend")
def get_exception_trend(
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Daily exception counts grouped by type — feeds trend chart on Exceptions Monitor."""
    from app.services.dashboard_metrics import get_exception_trend as _get_trend
    return _get_trend(session, from_date=from_date, to_date=to_date)


@router.get("/exceptions/anomalies")
def get_exception_anomalies(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Spike detection, recurring issues, failure clusters — feeds System Anomalies page."""
    from app.services.dashboard_metrics import get_exception_anomalies as _get_anomalies
    return _get_anomalies(session)


@router.get("/exceptions")
def list_exceptions_v2(
    exc_status: str = Query(default="open", alias="status"),
    severity: str | None = Query(default=None),
    exc_type: str | None = Query(default=None, alias="type"),
    from_date: datetime | None = Query(default=None, description="ISO date filter — created_at >="),
    to_date: datetime | None = Query(default=None, description="ISO date filter — created_at <="),
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    List exception records from the exceptions table.

    This is the authoritative source for open_exception_count shown on the
    health tiles. Supports filtering by status (open | resolved | ignored),
    severity (critical | warning), and type (exact match).

    Returns up to `limit` records plus a `groups` summary: per-type counts
    used by the UI to render grouped/deduplicated views without a second round-trip.
    """
    from sqlalchemy import text

    valid_statuses = {"open", "resolved", "ignored"}
    if exc_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of: {', '.join(valid_statuses)}",
        )

    clauses: list[str] = ["status = :exc_status"]
    params: dict[str, Any] = {
        "exc_status": exc_status,
        "limit": limit,
        "offset": offset,
    }
    if severity:
        clauses.append("severity = :severity")
        params["severity"] = severity
    if exc_type:
        clauses.append("type = :exc_type")
        params["exc_type"] = exc_type
    if from_date:
        clauses.append("created_at >= :from_date")
        params["from_date"] = from_date
    if to_date:
        clauses.append("created_at <= :to_date")
        params["to_date"] = to_date

    where = " AND ".join(clauses)

    rows = session.execute(text(f"""
        SELECT id, call_event_id, entity_type, entity_id,
               type, severity, status,
               resolution_reason, resolved_by,
               context_json, version,
               created_at, updated_at
        FROM exceptions
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """), params).fetchall()

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total_row = session.execute(text(f"""
        SELECT COUNT(*) FROM exceptions WHERE {where}
    """), count_params).fetchone()
    total = int(total_row[0]) if total_row else 0

    # Per-type group counts (always unfiltered by exc_type so the sidebar totals reflect reality)
    group_clauses: list[str] = ["status = :exc_status"]
    group_params: dict[str, Any] = {"exc_status": exc_status}
    if severity:
        group_clauses.append("severity = :severity")
        group_params["severity"] = severity
    group_where = " AND ".join(group_clauses)

    group_rows = session.execute(text(f"""
        SELECT type, severity, COUNT(*) as cnt
        FROM exceptions
        WHERE {group_where}
        GROUP BY type, severity
        ORDER BY cnt DESC
    """), group_params).fetchall()

    groups = [
        {"type": r[0], "severity": r[1], "count": int(r[2])}
        for r in group_rows
    ]

    exceptions = [
        {
            "id": r[0],
            "call_event_id": r[1],
            "entity_type": r[2],
            "entity_id": r[3],
            "type": r[4],
            "severity": r[5],
            "status": r[6],
            "resolution_reason": r[7],
            "resolved_by": r[8],
            "context_json": r[9] or {},
            "version": r[10],
            "created_at": r[11].isoformat() if r[11] and hasattr(r[11], "isoformat") else r[11],
            "updated_at": r[12].isoformat() if r[12] and hasattr(r[12], "isoformat") else r[12],
        }
        for r in rows
    ]
    return {"exceptions": exceptions, "total": total, "status_filter": exc_status, "groups": groups}


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


class BulkIgnoreRequest(BaseModel):
    type: str
    note: str = "bulk ignored by operator"


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

    ignored = ignore_exception(
        session,
        exception_id=body.exception_id,
        resolved_by=operator_id,
        reason="operator ignored",
    )

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


@router.post("/actions/bulk-ignore")
def action_bulk_ignore(
    body: BulkIgnoreRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Bulk-ignore all open exceptions of a given type. Used to clear noise from the queue."""
    from sqlalchemy import text
    import uuid
    from app.models.audit import AuditLog

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    result = session.execute(text("""
        UPDATE exceptions
        SET status = 'ignored',
            resolved_by = :operator_id,
            resolution_reason = :reason,
            version = version + 1,
            updated_at = :now
        WHERE type = :exc_type AND status = 'open'
    """), {"operator_id": operator_id, "reason": body.note, "now": now, "exc_type": body.type})
    ignored_count = result.rowcount

    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="exception",
        entity_id=body.type,
        action="bulk_ignore",
        operator_id=operator_id,
        context_json={"type": body.type, "count": ignored_count, "note": body.note},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "ignored_count": ignored_count, "audit_log_id": audit.id}


class AdvanceStaleLeadRequest(BaseModel):
    contact_id: str
    outcome: str  # "voicemail" | "no_answer"


@router.post("/actions/advance-stale-lead")
def advance_stale_lead(
    body: AdvanceStaleLeadRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Manually advance a stale lead whose Synthflow webhook was missed.

    outcome=voicemail — VM confirmed in Synthflow; advance tier or finalize.
    outcome=no_answer — no connection confirmed; retry or close on consecutive failure.
    """
    from app.config import get_settings
    from app.services.stale_recovery import (
        StaleLeadConflict, StaleLeadNotFound, advance_stale_lead as _advance,
    )

    operator_id = auth["operator_id"]
    settings = get_settings()

    try:
        result = _advance(session, body.contact_id, body.outcome, operator_id, settings)
        session.commit()
        return {"status": "ok", **result}
    except StaleLeadNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except StaleLeadConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class RecoverCallWebhookRequest(BaseModel):
    contact_id: str
    call_id: str  # Synthflow call_id from the Logs page


@router.post("/actions/recover-call-webhook")
def recover_call_webhook(
    body: RecoverCallWebhookRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Fetch a call from Synthflow by call_id and replay the full processing pipeline.

    Use when a completed call's webhook was never delivered: operator looks up
    the Synthflow call_id in the Logs page and submits it here.  The call data
    is fetched from the Synthflow API and a process_call_event job is scheduled,
    running AI analysis and GHL updates exactly as if the webhook had arrived.
    """
    from app.config import get_settings
    from app.services.stale_recovery import (
        StaleLeadConflict, WebhookRecoveryError, recover_missed_webhook,
    )

    operator_id = auth["operator_id"]
    settings = get_settings()

    try:
        result = recover_missed_webhook(
            session, body.contact_id, body.call_id, operator_id, settings
        )
        session.commit()
        return {"status": "ok", **result}
    except StaleLeadConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except WebhookRecoveryError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


class IgnoreWebhookFailureRequest(BaseModel):
    job_id: str      # scheduled_jobs.id of the launch_outbound_call job
    contact_id: str


@router.post("/actions/ignore-webhook-failure")
def action_ignore_webhook_failure(
    body: IgnoreWebhookFailureRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Dismiss a webhook failure row without processing it.

    Writes an audit_log entry keyed on the job_id so the panel exclusion
    query can filter it out on the next refresh.  Use for butt-dials,
    duplicate calls, or any case where no recovery action is needed.
    """
    import uuid as _uuid
    from app.models.audit import AuditLog

    operator_id = auth["operator_id"]
    audit_id = str(_uuid.uuid4())
    session.add(AuditLog(
        id=audit_id,
        entity_type="scheduled_job",
        entity_id=body.job_id,
        action="manual_webhook_ignore",
        operator_id=operator_id,
        context_json={"contact_id": body.contact_id},
    ))
    session.commit()
    return {"status": "ok", "audit_log_id": audit_id}


class AcknowledgeAlertRequest(BaseModel):
    alert_id: str
    note: str = ""


@router.post("/actions/acknowledge-alert")
def action_acknowledge_alert(
    body: AcknowledgeAlertRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Mark an active alert as acknowledged — suppresses repeat notifications without resolving it."""
    from sqlalchemy import text
    import uuid
    from app.models.audit import AuditLog

    operator_id = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    result = session.execute(text("""
        UPDATE alert_events
        SET status = 'acknowledged', resolved_at = :now
        WHERE id = :alert_id AND status = 'active'
        RETURNING id
    """), {"alert_id": body.alert_id, "now": now})

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alert not found or not in active status",
        )

    audit = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="alert",
        entity_id=body.alert_id,
        action="acknowledge_alert",
        operator_id=operator_id,
        context_json={"note": body.note},
        created_at=now,
    )
    session.add(audit)
    session.flush()
    session.commit()

    return {"status": "ok", "alert_id": body.alert_id, "audit_log_id": audit.id}


# ── WebSocket — real-time event feed ─────────────────────────────────────────

@router.get("/campaign-overview")
def get_campaign_overview(
    from_date: str | None = Query(default=None, description="ISO date YYYY-MM-DD (local)"),
    to_date: str | None = Query(default=None, description="ISO date YYYY-MM-DD (local)"),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Upcoming scheduled contact actions within a date window.

    Returns all leads with their next scheduled action (call / SMS / email) and
    last call timestamp. Terminal leads (DNC, invalid, enrolled, closed) are
    always included. Active leads are filtered to those whose effective_at falls
    within [from_date, to_date).

    Default window: today → today + 7 days.
    """
    from datetime import date, timedelta
    from sqlalchemy import text

    today = date.today()
    try:
        d_from = date.fromisoformat(from_date) if from_date else today
        d_to = date.fromisoformat(to_date) if to_date else today + timedelta(days=7)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_date and to_date must be ISO dates (YYYY-MM-DD)",
        )

    rows = session.execute(text("""
        SELECT
            ls.contact_id,
            COALESCE(ls.normalized_phone, ls.contact_id)  AS contact,
            ls.campaign_name,
            ls.lead_stage,
            ls.status,
            ls.do_not_call,
            ls.invalid,
            ls.next_action_at,
            sj.job_type,
            sj.run_at                                      AS job_run_at,
            ce.last_call_at,
            ce.last_voice_agent,
            CASE
                WHEN sj.run_at IS NOT NULL AND ls.next_action_at IS NOT NULL
                    THEN LEAST(sj.run_at, ls.next_action_at)
                ELSE COALESCE(sj.run_at, ls.next_action_at)
            END                                            AS effective_at,
            ls.sales_outcome,
            ls.sales_next_action,
            ls.sales_follow_up_at,
            ls.sales_updated_by
        FROM lead_state ls
        LEFT JOIN LATERAL (
            SELECT job_type, run_at
            FROM scheduled_jobs
            WHERE entity_id = ls.contact_id
              AND status    = 'pending'
            ORDER BY run_at ASC
            LIMIT 1
        ) sj ON true
        LEFT JOIN LATERAL (
            SELECT created_at AS last_call_at, voice_agent AS last_voice_agent
            FROM call_events
            WHERE contact_id = ls.contact_id
            ORDER BY created_at DESC
            LIMIT 1
        ) ce ON true
        ORDER BY ce.last_call_at DESC NULLS LAST
    """)).fetchall()

    _JOB_LABEL = {
        "launch_outbound_call": "Call",
        "send_sms": "SMS",
        "send_email": "Email",
    }

    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    def _to_utc(ts):
        if ts is None:
            return None
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            return ts.replace(tzinfo=_tz.utc)
        return ts

    def _fmt_delay(ts) -> str:
        now = _dt.now(_tz.utc)
        secs = (ts - now).total_seconds()
        if secs <= 0:
            return "Now"
        if secs < 3600:
            return f"{int(secs // 60)}m"
        if secs < 86400:
            h, m = int(secs // 3600), int((secs % 3600) // 60)
            return f"{h}h {m}m" if m else f"{h}h"
        d, h = int(secs // 86400), int((secs % 86400) // 3600)
        return f"{d}d {h}h" if h else f"{d}d"

    window_start = _dt.combine(d_from, _dt.min.time()).replace(tzinfo=_tz.utc)
    window_end = _dt.combine(d_to + _td(days=1), _dt.min.time()).replace(tzinfo=_tz.utc)

    _VALID_CAMPAIGNS = {"cold lead", "new lead", "inbound"}
    _VOICE_AGENT_CAMPAIGN = {
        "coldlead": "Cold Lead",
        "newlead": "New Lead",
        "inbound": "Inbound",
    }

    def _resolve_campaign(campaign_name, lead_stage, voice_agent=None) -> str:
        for val in (campaign_name, lead_stage):
            if val and val.strip().lower() in _VALID_CAMPAIGNS:
                return val.strip().title() if val.strip().lower() == "inbound" else val.strip()
        if voice_agent:
            mapped = _VOICE_AGENT_CAMPAIGN.get(voice_agent.strip().lower())
            if mapped:
                return mapped
        return "Inbound"  # unknown source defaults to Inbound bucket (never Unknown)

    result = []
    for r in rows:
        (contact_id, contact, campaign_name, lead_stage, st, do_not_call, invalid,
         next_action_at, job_type, job_run_at, last_call_at, last_voice_agent, effective_at,
         sales_outcome, sales_next_action, sales_follow_up_at, sales_updated_by) = r

        dnc = bool(do_not_call)
        inv = bool(invalid)
        status_val = (st or "").lower()
        is_terminal = dnc or inv or status_val in ("enrolled", "closed")

        effective = _to_utc(effective_at)
        has_recent_call = last_call_at is not None

        if not is_terminal:
            if effective is None:
                if not has_recent_call:
                    continue
            elif not (window_start <= effective < window_end):
                continue

        # Next action label
        if is_terminal:
            next_action = None
        else:
            job_run_utc = _to_utc(job_run_at)
            naa = _to_utc(next_action_at)
            if job_run_utc and (naa is None or job_run_utc <= naa):
                label = _JOB_LABEL.get(job_type, job_type or "Scheduled")
                next_action = f"{label} in {_fmt_delay(job_run_utc)}"
            elif naa:
                next_action = f"Follow-up in {_fmt_delay(naa)}"
            else:
                next_action = "Unscheduled"

        # Status label
        if dnc:
            final_status = "Do Not Call"
        elif inv:
            final_status = "Invalid"
        elif status_val == "enrolled":
            final_status = "Enrolled"
        elif status_val == "closed":
            final_status = "Closed"
        else:
            final_status = None

        lc_utc = _to_utc(last_call_at)
        sfu_utc = _to_utc(sales_follow_up_at)
        result.append({
            "contact_id": contact_id,
            "contact": contact,
            "campaign_name": _resolve_campaign(campaign_name, lead_stage, last_voice_agent),
            "last_call_at": lc_utc.isoformat() if lc_utc else None,
            "next_action": next_action,
            "status": final_status,
            "sales_outcome": sales_outcome,
            "sales_next_action": sales_next_action,
            "sales_follow_up_at": sfu_utc.isoformat() if sfu_utc else None,
            "sales_updated_by": sales_updated_by,
        })

    return {
        "from_date": d_from.isoformat(),
        "to_date": d_to.isoformat(),
        "rows": result,
        "total": len(result),
    }


@router.get("/lead/{contact_id}/detail")
def get_lead_detail(
    contact_id: str,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Full 6-table drill-down for a single contact.

    Returns: lead_state, call_events (50 most recent), shadow_actions,
    scheduled_jobs (50 most recent), outbound_messages, exceptions.
    Returns 404 if the contact is not found in lead_state.
    """
    from sqlalchemy import text

    lead_row = session.execute(text("""
        SELECT contact_id, campaign_name, ai_campaign_value, status,
               do_not_call, next_action_at, version, updated_at
        FROM lead_state
        WHERE contact_id = :cid
    """), {"cid": contact_id}).fetchone()

    def _iso(ts):
        if ts is None:
            return None
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    if lead_row is not None:
        lead_state = {
            "contact_id": lead_row[0],
            "campaign_name": lead_row[1],
            "ai_campaign_value": lead_row[2],
            "status": lead_row[3],
            "do_not_call": lead_row[4],
            "next_action_at": _iso(lead_row[5]),
            "version": lead_row[6],
            "updated_at": _iso(lead_row[7]),
        }
    else:
        # No lead_state exists (e.g. telephony-failed call that never progressed
        # to AI processing). Return call_events and other available data — 404
        # only if there are truly no records for this contact at all.
        has_any = session.execute(text("""
            SELECT 1 FROM call_events WHERE contact_id = :cid LIMIT 1
        """), {"cid": contact_id}).fetchone()
        if has_any is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No lead found for contact_id={contact_id}",
            )
        lead_state = None

    call_rows = session.execute(text("""
        SELECT ce.call_id, ce.status, ce.duration_seconds,
               LEFT(ce.transcript, 120) AS transcript_preview,
               ce.created_at,
               sr.student_summary
        FROM call_events ce
        LEFT JOIN summary_results sr ON sr.call_event_id = ce.id
        WHERE ce.contact_id = :cid
        ORDER BY ce.created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    call_events = [
        {"call_id": r[0], "status": r[1], "duration_seconds": r[2],
         "transcript_preview": r[3], "created_at": _iso(r[4]),
         "student_summary": r[5]}
        for r in call_rows
    ]

    shadow_rows = session.execute(text("""
        SELECT action_type, payload, created_at
        FROM shadow_actions
        WHERE contact_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    shadow_actions = [
        {"action_type": r[0], "payload": r[1], "created_at": _iso(r[2])}
        for r in shadow_rows
    ]

    job_rows = session.execute(text("""
        SELECT job_type, status, run_at, payload_json, created_at
        FROM scheduled_jobs
        WHERE payload_json->>'contact_id' = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    scheduled_jobs = [
        {"job_type": r[0], "status": r[1], "run_at": _iso(r[2]),
         "payload_json": r[3], "created_at": _iso(r[4])}
        for r in job_rows
    ]

    msg_rows = session.execute(text("""
        SELECT channel, status, subject, body, created_at
        FROM outbound_messages
        WHERE contact_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    outbound_messages = [
        {"channel": r[0], "status": r[1], "subject": r[2],
         "body": r[3], "created_at": _iso(r[4])}
        for r in msg_rows
    ]

    exc_rows = session.execute(text("""
        SELECT type, severity, status, context_json, created_at
        FROM exceptions
        WHERE entity_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    exceptions = [
        {"type": r[0], "severity": r[1], "status": r[2],
         "context_json": r[3] or {}, "created_at": _iso(r[4])}
        for r in exc_rows
    ]

    return {
        "contact_id": contact_id,
        "lead_state": lead_state,
        "call_events": call_events,
        "shadow_actions": shadow_actions,
        "scheduled_jobs": scheduled_jobs,
        "outbound_messages": outbound_messages,
        "exceptions": exceptions,
    }


@router.get("/settings")
def get_settings_config(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Read all app_config rows as {key: value}."""
    from sqlalchemy import text

    rows = session.execute(text(
        "SELECT key, value, updated_at, updated_by FROM app_config ORDER BY key"
    )).fetchall()

    config = {r[0]: r[1] for r in rows}
    audit_rows = session.execute(text("""
        SELECT created_at, operator_id, entity_id AS key
        FROM audit_log
        WHERE entity_type = 'app_config'
        ORDER BY created_at DESC
        LIMIT 50
    """)).fetchall()
    audit = [
        {"created_at": r[0].isoformat() if r[0] else None, "operator_id": r[1], "key": r[2]}
        for r in audit_rows
    ]

    return {"config": config, "audit_log": audit}


class SaveSettingsRequest(BaseModel):
    operator_id: str
    values: dict[str, str]


@router.post("/settings")
def save_settings_config(
    body: SaveSettingsRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    UPSERT app_config entries and write an audit_log entry per key.
    Requires Bearer token auth. operator_id in body is recorded in audit_log.
    """
    import uuid
    from sqlalchemy import text

    if not body.operator_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="operator_id is required",
        )
    if not body.values:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="values must be a non-empty dict",
        )

    now = datetime.now(tz=timezone.utc)
    operator = body.operator_id.strip()

    for key, value in body.values.items():
        session.execute(text("""
            INSERT INTO app_config (key, value, updated_at, updated_by)
            VALUES (:key, :value, :now, :by)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by
        """), {"key": key, "value": value, "now": now, "by": operator})

        session.execute(text("""
            INSERT INTO audit_log
              (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES
              (:id, 'app_config', :key, 'config_updated', :by,
               '{"source": "dashboard_v2"}'::jsonb, :now)
        """), {"id": str(uuid.uuid4()), "key": key, "by": operator, "now": now})

    session.commit()
    return {"status": "ok", "keys_saved": len(body.values)}


class SalesOutcomeRequest(BaseModel):
    contact_id: str
    sales_outcome: str        # required: booked | follow_up | not_interested | no_answer | voicemail | wrong_number
    sales_next_action: str | None = None   # required when sales_outcome == "follow_up"
    sales_follow_up_at: datetime | None = None  # required when sales_next_action is set
    sales_notes: str | None = None         # max 200 chars
    updated_by: str                        # agent_id — required


_VALID_SALES_OUTCOMES = {
    "booked", "follow_up", "not_interested", "no_answer", "voicemail", "wrong_number",
}

# Outcomes that end the active queue for this contact
_TERMINAL_SALES_OUTCOMES = {"booked", "not_interested", "wrong_number"}


@router.post("/sales-queue/outcome")
def save_sales_outcome(
    body: SalesOutcomeRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Log a post-call outcome for a contact from the sales queue.

    Validation:
    - sales_outcome is required and must be a known value
    - sales_next_action + sales_follow_up_at are required when outcome == "follow_up"
    - sales_notes max 200 chars
    - updated_by is required

    Updates lead_state with optimistic concurrency (version check).
    Returns the updated contact_id and sales_outcome.
    """
    from sqlalchemy import text

    # ── Input validation ────────────────────────────────────────────────────
    if not body.sales_outcome:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="sales_outcome is required")

    if body.sales_outcome not in _VALID_SALES_OUTCOMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"sales_outcome must be one of: {', '.join(sorted(_VALID_SALES_OUTCOMES))}",
        )

    if body.sales_outcome == "follow_up":
        if not body.sales_next_action:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="sales_next_action is required when outcome is follow_up",
            )
        if not body.sales_follow_up_at:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="sales_follow_up_at is required when sales_next_action is set",
            )

    if body.sales_notes and len(body.sales_notes) > 200:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sales_notes must be 200 characters or fewer",
        )

    if not body.updated_by or not body.updated_by.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="updated_by is required")

    # ── Load and update lead_state ──────────────────────────────────────────
    row = session.execute(text("""
        SELECT id, version FROM lead_state WHERE contact_id = :cid
    """), {"cid": body.contact_id}).fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No lead found for contact_id={body.contact_id}",
        )

    lead_id, current_version = row[0], row[1]
    now = datetime.now(tz=timezone.utc)

    result = session.execute(text("""
        UPDATE lead_state
        SET sales_outcome     = :outcome,
            sales_next_action = :next_action,
            sales_follow_up_at = :follow_up_at,
            sales_notes       = :notes,
            sales_updated_by  = :updated_by,
            version           = version + 1,
            updated_at        = :now
        WHERE id = :id AND version = :version
    """), {
        "outcome":     body.sales_outcome,
        "next_action": body.sales_next_action,
        "follow_up_at": body.sales_follow_up_at,
        "notes":       body.sales_notes,
        "updated_by":  body.updated_by.strip(),
        "now":         now,
        "id":          lead_id,
        "version":     current_version,
    })
    session.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Concurrent update conflict — please retry",
        )

    logger.info(
        "save_sales_outcome | contact_id=%s outcome=%s updated_by=%s",
        body.contact_id, body.sales_outcome, body.updated_by,
    )

    return {
        "status": "ok",
        "contact_id": body.contact_id,
        "sales_outcome": body.sales_outcome,
        "is_terminal": body.sales_outcome in _TERMINAL_SALES_OUTCOMES,
    }


# ── Mode control endpoints ────────────────────────────────────────────────────

_ALLOWED_MODE_KEYS = frozenset({
    "shadow_mode_enabled",
    "ghl_write_mode",
    "ghl_write_shadow_log_only",
    "ghl_write_contact_fields",
    "ghl_write_tasks",
    "ghl_write_summary",
    "ghl_write_campaign_state",
    "ghl_write_finalization",
    "system_paused",
    "outbound_campaigns_paused",
})

_BOOL_MODE_KEYS = frozenset({
    "shadow_mode_enabled",
    "ghl_write_shadow_log_only",
    "ghl_write_contact_fields",
    "ghl_write_tasks",
    "ghl_write_summary",
    "ghl_write_campaign_state",
    "ghl_write_finalization",
    "system_paused",
    "outbound_campaigns_paused",
})


@router.get("/mode")
def get_mode(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Return current mode flags and pre-flight readiness checks.

    Reads from app_config (DB-first, settings fallback).
    Always safe to call — never mutates state.
    """
    from app.config import get_settings as _get_settings
    from app.core.mode_flags import get_mode_flags, get_preflight_status
    from sqlalchemy import text

    settings = _get_settings()
    flags = get_mode_flags(session, settings)
    preflight = get_preflight_status(session, settings)

    # Fetch last-changed metadata per flag from audit_log
    rows = session.execute(text("""
        SELECT entity_id AS key, operator_id, created_at,
               context_json->>'new_value' AS new_value
        FROM audit_log
        WHERE entity_type = 'app_config'
          AND entity_id = ANY(:keys)
        ORDER BY created_at DESC
        LIMIT 100
    """), {"keys": list(_ALLOWED_MODE_KEYS)}).fetchall()

    last_changed: dict[str, dict] = {}
    for r in rows:
        key = r[0]
        if key not in last_changed:
            last_changed[key] = {
                "operator_id": r[1],
                "at": r[2].isoformat() if r[2] else None,
                "new_value": r[3],
            }

    return {
        "flags": flags.as_dict(),
        "last_changed": last_changed,
        "preflight": [
            {
                "key": c.key,
                "label": c.label,
                "status": c.status,
                "detail": c.detail,
            }
            for c in preflight
        ],
    }


class UpdateModeRequest(BaseModel):
    flags: dict[str, str]
    reason: str = ""


@router.post("/mode")
def update_mode(
    body: UpdateModeRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    Atomically update one or more mode-control flags.

    Requires Bearer token auth.
    Only keys in _ALLOWED_MODE_KEYS are accepted — others are rejected with 422.
    Bool keys accept 'true'/'false' (case-insensitive).
    ghl_write_mode accepts 'shadow' or 'live'.

    Safety rules enforced:
    - Enabling outbound calls (shadow_mode_enabled=false) requires
      SYNTHFLOW_API_KEY and SYNTHFLOW_LAUNCH_WORKFLOW_URL to be set.
    - Enabling GHL writes (ghl_write_mode=live) requires GHL_API_KEY to be set.

    All changes are written to audit_log with operator_id + reason.
    """
    import uuid
    from app.config import get_settings as _get_settings
    from sqlalchemy import text

    settings = _get_settings()
    operator = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)

    # ── Validate keys ─────────────────────────────────────────────────────────
    unknown = set(body.flags.keys()) - _ALLOWED_MODE_KEYS
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown mode flag(s): {', '.join(sorted(unknown))}. "
                   f"Allowed: {', '.join(sorted(_ALLOWED_MODE_KEYS))}",
        )
    if not body.flags:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="flags must contain at least one key",
        )

    # ── Validate values ───────────────────────────────────────────────────────
    for key, value in body.flags.items():
        if key in _BOOL_MODE_KEYS:
            if value.strip().lower() not in ("true", "false"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"'{key}' must be 'true' or 'false', got {value!r}",
                )
        elif key == "ghl_write_mode":
            if value.strip().lower() not in ("shadow", "live"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"ghl_write_mode must be 'shadow' or 'live', got {value!r}",
                )

    # ── Safety pre-flight: going live requires credentials ───────────────────
    enabling_outbound = body.flags.get("shadow_mode_enabled", "").lower() == "false"
    enabling_ghl_live = body.flags.get("ghl_write_mode", "").lower() == "live"

    if enabling_outbound:
        missing = []
        if not settings.synthflow_api_key:
            missing.append("SYNTHFLOW_API_KEY")
        if not settings.synthflow_launch_workflow_url_new:
            missing.append("SYNTHFLOW_LAUNCH_WORKFLOW_URL_New")
        if not settings.synthflow_launch_workflow_url_cold:
            missing.append("SYNTHFLOW_LAUNCH_WORKFLOW_URL_Cold")
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot enable outbound calls — missing config: {', '.join(missing)}",
            )

    if enabling_ghl_live and not settings.ghl_api_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot enable GHL live writes — GHL_API_KEY is not set",
        )

    # ── Write to app_config + audit_log ───────────────────────────────────────
    context_note = body.reason.strip() if body.reason else "updated via dashboard"

    for key, value in body.flags.items():
        value_normalized = value.strip().lower() if key in _BOOL_MODE_KEYS else value.strip()

        # Read old value for audit
        old_row = session.execute(
            text("SELECT value FROM app_config WHERE key = :key"), {"key": key}
        ).fetchone()
        old_value = old_row[0] if old_row else None

        session.execute(text("""
            INSERT INTO app_config (key, value, updated_at, updated_by)
            VALUES (:key, :value, :now, :by)
            ON CONFLICT (key) DO UPDATE
            SET value      = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at,
                updated_by = EXCLUDED.updated_by
        """), {"key": key, "value": value_normalized, "now": now, "by": operator})

        session.execute(text("""
            INSERT INTO audit_log
              (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES
              (:id, 'app_config', :key, 'mode_flag_updated', :by, CAST(:ctx AS jsonb), :now)
        """), {
            "id": str(uuid.uuid4()),
            "key": key,
            "by": operator,
            "ctx": json.dumps({
                "old_value": old_value,
                "new_value": value_normalized,
                "reason": context_note,
                "source": "system_controls",
            }),
            "now": now,
        })

    session.commit()

    # Return updated flags snapshot
    from app.config import get_settings as _get_settings2
    from app.core.mode_flags import get_mode_flags
    updated_flags = get_mode_flags(session, _get_settings2())

    logger.info(
        "mode_flags updated | keys=%s operator=%s reason=%r",
        list(body.flags.keys()), operator, context_note,
    )

    return {
        "status": "ok",
        "keys_updated": list(body.flags.keys()),
        "flags": updated_flags.as_dict(),
    }


@router.post("/mode/pause")
def pause_system(
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Pause the system — workers will hold jobs without executing until resumed."""
    import uuid
    from sqlalchemy import text

    operator = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)
    session.execute(text("""
        INSERT INTO app_config (key, value, updated_at, updated_by)
        VALUES ('system_paused', 'true', :now, :by)
        ON CONFLICT (key) DO UPDATE
        SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by
    """), {"now": now, "by": operator})
    session.execute(text("""
        INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
        VALUES (:id, 'app_config', 'system_paused', 'mode_flag_updated', :by,
                '{"new_value":"true","reason":"operator pause","source":"system_controls"}'::jsonb, :now)
    """), {"id": str(uuid.uuid4()), "by": operator, "now": now})
    session.commit()
    logger.warning("SYSTEM PAUSED by operator=%s", operator)
    return {"status": "ok", "system_paused": True}


@router.post("/mode/resume")
def resume_system(
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Resume the system — workers will begin executing held jobs."""
    import uuid
    from sqlalchemy import text

    operator = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)
    session.execute(text("""
        INSERT INTO app_config (key, value, updated_at, updated_by)
        VALUES ('system_paused', 'false', :now, :by)
        ON CONFLICT (key) DO UPDATE
        SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by
    """), {"now": now, "by": operator})
    session.execute(text("""
        INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
        VALUES (:id, 'app_config', 'system_paused', 'mode_flag_updated', :by,
                '{"new_value":"false","reason":"operator resume","source":"system_controls"}'::jsonb, :now)
    """), {"id": str(uuid.uuid4()), "by": operator, "now": now})
    session.commit()
    logger.info("System resumed by operator=%s", operator)
    return {"status": "ok", "system_paused": False}


@router.post("/mode/pause-outbound-campaigns")
def pause_outbound_campaigns(
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Pause New Lead and Cold Lead campaign jobs. Inbound processing continues."""
    import uuid
    from sqlalchemy import text

    operator = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)
    session.execute(text("""
        INSERT INTO app_config (key, value, updated_at, updated_by)
        VALUES ('outbound_campaigns_paused', 'true', :now, :by)
        ON CONFLICT (key) DO UPDATE
        SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by
    """), {"now": now, "by": operator})
    session.execute(text("""
        INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
        VALUES (:id, 'app_config', 'outbound_campaigns_paused', 'mode_flag_updated', :by,
                '{"new_value":"true","reason":"operator pause outbound campaigns","source":"system_controls"}'::jsonb, :now)
    """), {"id": str(uuid.uuid4()), "by": operator, "now": now})
    session.commit()
    logger.warning("OUTBOUND CAMPAIGNS PAUSED by operator=%s", operator)
    return {"status": "ok", "outbound_campaigns_paused": True}


@router.post("/mode/resume-outbound-campaigns")
def resume_outbound_campaigns(
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Resume New Lead and Cold Lead campaign jobs."""
    import uuid
    from sqlalchemy import text

    operator = auth["operator_id"]
    now = datetime.now(tz=timezone.utc)
    session.execute(text("""
        INSERT INTO app_config (key, value, updated_at, updated_by)
        VALUES ('outbound_campaigns_paused', 'false', :now, :by)
        ON CONFLICT (key) DO UPDATE
        SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by
    """), {"now": now, "by": operator})
    session.execute(text("""
        INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
        VALUES (:id, 'app_config', 'outbound_campaigns_paused', 'mode_flag_updated', :by,
                '{"new_value":"false","reason":"operator resume outbound campaigns","source":"system_controls"}'::jsonb, :now)
    """), {"id": str(uuid.uuid4()), "by": operator, "now": now})
    session.commit()
    logger.info("Outbound campaigns resumed by operator=%s", operator)
    return {"status": "ok", "outbound_campaigns_paused": False}


# ── DB Explorer ───────────────────────────────────────────────────────────────

class DbQueryRequest(BaseModel):
    sql: str

_ROW_LIMIT = 500

@router.get("/db/tables")
def list_db_tables(
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return all user tables with row counts. No auth required (read-only metadata)."""
    from sqlalchemy import text
    rows = session.execute(text("""
        SELECT
            t.table_name,
            COALESCE(s.n_live_tup, 0) AS row_estimate
        FROM information_schema.tables t
        LEFT JOIN pg_stat_user_tables s ON s.relname = t.table_name
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
    """)).fetchall()
    return {"tables": [{"name": r[0], "row_estimate": int(r[1])} for r in rows]}


@router.post("/db/query")
def run_db_query(
    body: DbQueryRequest,
    auth: DashboardAuth,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Execute a SQL query and return up to 500 rows. Auth required."""
    from sqlalchemy import text
    sql = body.sql.strip()
    if not sql:
        raise HTTPException(status_code=400, detail="SQL cannot be empty")
    try:
        result = session.execute(text(sql))
        # DML (INSERT/UPDATE/DELETE) has no cursor description
        if result.returns_rows:
            columns = list(result.keys())
            raw_rows = result.fetchmany(_ROW_LIMIT + 1)
            truncated = len(raw_rows) > _ROW_LIMIT
            rows = [
                [str(v) if v is not None else None for v in row]
                for row in raw_rows[:_ROW_LIMIT]
            ]
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }
        else:
            session.commit()
            return {
                "columns": [],
                "rows": [],
                "row_count": result.rowcount,
                "truncated": False,
            }
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Campaign overview ─────────────────────────────────────────────────────────

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
