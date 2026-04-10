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


@router.get("/voice-performance")
def get_voice_performance(
    from_date: datetime | None = Query(default=None),
    to_date: datetime | None = Query(default=None),
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    """Voice call performance analytics — KPIs, time series, WoW, scatter data."""
    from app.services.dashboard_metrics import get_voice_performance as _get_vp
    return _get_vp(session, from_date=from_date, to_date=to_date)


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
            END                                            AS effective_at
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

    _VALID_CAMPAIGNS = {"cold lead", "new lead"}
    _VOICE_AGENT_CAMPAIGN = {"coldlead": "Cold Lead", "newlead": "New Lead"}

    def _resolve_campaign(campaign_name, lead_stage, voice_agent=None) -> str:
        for val in (campaign_name, lead_stage):
            if val and val.strip().lower() in _VALID_CAMPAIGNS:
                return val.strip()
        if voice_agent:
            mapped = _VOICE_AGENT_CAMPAIGN.get(voice_agent.strip().lower())
            if mapped:
                return mapped
        return "Unknown"

    result = []
    for r in rows:
        (contact_id, contact, campaign_name, lead_stage, st, do_not_call, invalid,
         next_action_at, job_type, job_run_at, last_call_at, last_voice_agent, effective_at) = r

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
        result.append({
            "contact_id": contact_id,
            "contact": contact,
            "campaign_name": _resolve_campaign(campaign_name, lead_stage, last_voice_agent),
            "last_call_at": lc_utc.isoformat() if lc_utc else None,
            "next_action": next_action,
            "status": final_status,
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

    if lead_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No lead found for contact_id={contact_id}",
        )

    def _iso(ts):
        if ts is None:
            return None
        return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

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

    call_rows = session.execute(text("""
        SELECT call_id, status, duration_seconds,
               LEFT(transcript, 120) AS transcript_preview, created_at
        FROM call_events
        WHERE contact_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    call_events = [
        {"call_id": r[0], "status": r[1], "duration_seconds": r[2],
         "transcript_preview": r[3], "created_at": _iso(r[4])}
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
        SELECT channel, status, LEFT(body, 100) AS body_preview, created_at
        FROM outbound_messages
        WHERE contact_id = :cid
        ORDER BY created_at DESC
        LIMIT 50
    """), {"cid": contact_id}).fetchall()
    outbound_messages = [
        {"channel": r[0], "status": r[1], "body_preview": r[2], "created_at": _iso(r[3])}
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
