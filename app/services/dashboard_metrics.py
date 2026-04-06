"""
Dashboard metrics service — computes health and KPI aggregates from Postgres.

All queries target existing tables only. No external calls.
Used by GET /dashboard/health, GET /dashboard/metrics, and
GET /dashboard/voice-performance.

Design rules:
  - Each query is simple and targeted (no joins across more than 3 tables).
  - All results are typed dicts for direct JSON serialization.
  - Division-by-zero is handled explicitly (returns None, not 0.0).
  - Queries use parameterized SQL via SQLAlchemy text() for safety.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Stuck job threshold: pending job past-due by more than this many minutes.
STUCK_JOB_THRESHOLD_MINUTES = 10

# Voicemail statuses for rate calculations.
_VOICEMAIL_STATUSES = (
    "voicemail", "hangup_on_voicemail", "left_voicemail",
    "voicemail_detected", "machine_detected",
)


def get_health(session: Session) -> dict[str, Any]:
    """
    Compute the current system health snapshot directly from source tables.
    Returns a dict matching the GET /dashboard/health response schema.
    """
    from app.config import get_settings
    settings = get_settings()

    now = datetime.now(tz=timezone.utc)
    window_5m = now - timedelta(minutes=5)

    # Queue lag: age of the oldest past-due pending job
    lag_row = session.execute(text("""
        SELECT EXTRACT(EPOCH FROM (NOW() - MIN(run_at)))::float AS lag_seconds
        FROM scheduled_jobs
        WHERE status = 'pending' AND run_at <= NOW()
    """)).fetchone()
    queue_lag = float(lag_row[0]) if lag_row and lag_row[0] is not None else 0.0

    # Active workers: distinct claimed_by with valid lease
    workers_row = session.execute(text("""
        SELECT COUNT(DISTINCT claimed_by) AS cnt
        FROM scheduled_jobs
        WHERE status = 'running' AND lease_expires_at > NOW()
    """)).fetchone()
    active_workers = int(workers_row[0]) if workers_row else 0

    # Open exceptions
    exc_row = session.execute(text(
        "SELECT COUNT(*) FROM exceptions WHERE status = 'open'"
    )).fetchone()
    open_exception_count = int(exc_row[0]) if exc_row else 0

    # Stuck jobs
    stuck_row = session.execute(text("""
        SELECT COUNT(*) FROM scheduled_jobs
        WHERE status = 'pending'
          AND run_at < NOW() - INTERVAL '10 minutes'
    """)).fetchone()
    stuck_job_count = int(stuck_row[0]) if stuck_row else 0

    # Expired leases
    expired_row = session.execute(text("""
        SELECT COUNT(*) FROM scheduled_jobs
        WHERE status = 'running' AND lease_expires_at < NOW()
    """)).fetchone()
    expired_lease_count = int(expired_row[0]) if expired_row else 0

    # Job throughput last 5 min
    completed_row = session.execute(text("""
        SELECT COUNT(*) FROM scheduled_jobs
        WHERE status = 'completed' AND updated_at >= :window
    """), {"window": window_5m}).fetchone()
    jobs_completed_5m = int(completed_row[0]) if completed_row else 0

    failed_row = session.execute(text("""
        SELECT COUNT(*) FROM scheduled_jobs
        WHERE status = 'failed' AND updated_at >= :window
    """), {"window": window_5m}).fetchone()
    jobs_failed_5m = int(failed_row[0]) if failed_row else 0

    error_rate = (
        round(jobs_failed_5m / jobs_completed_5m, 4)
        if jobs_completed_5m > 0
        else None
    )

    return {
        "queue_lag_seconds": round(queue_lag, 1),
        "active_workers": active_workers,
        "open_exception_count": open_exception_count,
        "stuck_job_count": stuck_job_count,
        "expired_lease_count": expired_lease_count,
        "jobs_completed_last_5m": jobs_completed_5m,
        "jobs_failed_last_5m": jobs_failed_5m,
        "error_rate": error_rate,
        "shadow_mode_enabled": settings.shadow_mode_enabled,
        "ghl_write_mode": settings.ghl_write_mode,
        "app_env": settings.app_env,
        "recorded_at": now.isoformat(),
    }


def get_metrics(
    session: Session,
    campaign: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Compute aggregated KPIs, queue detail, and CRM metrics for a time window.
    Returns a dict matching the GET /dashboard/metrics response schema.
    """
    now = datetime.now(tz=timezone.utc)
    from_dt = from_date or (now - timedelta(days=7))
    to_dt = to_date or now

    params: dict[str, Any] = {"from_dt": from_dt, "to_dt": to_dt}
    campaign_filter = ""
    if campaign:
        campaign_filter = "AND ls.campaign_name = :campaign"
        params["campaign"] = campaign

    # ── KPIs ─────────────────────────────────────────────────────────────────
    kpi_sql = f"""
        SELECT
            COUNT(*)                                              AS total_calls,
            COUNT(*) FILTER (WHERE ce.status = 'completed')      AS completed,
            COUNT(*) FILTER (WHERE ce.status IN (
                'voicemail','hangup_on_voicemail','left_voicemail',
                'voicemail_detected','machine_detected'
            ))                                                    AS voicemail,
            COUNT(*) FILTER (WHERE ce.status = 'failed')         AS failed
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
        {campaign_filter}
    """
    kpi_row = session.execute(text(kpi_sql), params).fetchone()
    total = int(kpi_row[0]) if kpi_row else 0
    completed_n = int(kpi_row[1]) if kpi_row else 0
    voicemail_n = int(kpi_row[2]) if kpi_row else 0
    failed_n = int(kpi_row[3]) if kpi_row else 0

    pickup_rate = round(completed_n / total, 4) if total else None
    voicemail_rate = round(voicemail_n / total, 4) if total else None
    failed_rate = round(failed_n / total, 4) if total else None

    enrolled_row = session.execute(text("""
        SELECT COUNT(*) FROM call_events
        WHERE detected_intent = 'enrolled'
          AND created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()
    enrolled_count = int(enrolled_row[0]) if enrolled_row else 0

    dnc_row = session.execute(text("""
        SELECT COUNT(*) FROM lead_state WHERE do_not_call = true
    """)).fetchone()
    do_not_call_count = int(dnc_row[0]) if dnc_row else 0
    do_not_call_rate = (
        round(do_not_call_count / total, 4) if total else None
    )

    # ── AI metrics ────────────────────────────────────────────────────────────
    blank_row = session.execute(text("""
        SELECT COUNT(*) FROM call_events
        WHERE (transcript IS NULL OR transcript = '')
          AND created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()
    blank_n = int(blank_row[0]) if blank_row else 0
    blank_transcript_rate = round(blank_n / total, 4) if total else None

    intent_rows = session.execute(text("""
        SELECT detected_intent, COUNT(*) AS cnt
        FROM call_events
        WHERE detected_intent IS NOT NULL
          AND created_at BETWEEN :from_dt AND :to_dt
        GROUP BY detected_intent
        ORDER BY cnt DESC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()
    intent_distribution = {r[0]: int(r[1]) for r in intent_rows}

    consent_rows = session.execute(text("""
        SELECT summary_consent, COUNT(*) AS cnt
        FROM summary_results
        WHERE created_at BETWEEN :from_dt AND :to_dt
        GROUP BY summary_consent
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()
    consent_distribution = {r[0]: int(r[1]) for r in consent_rows}

    # ── Stuck and expired jobs ────────────────────────────────────────────────
    stuck_rows = session.execute(text("""
        SELECT id, job_type,
               payload_json->>'contact_id' AS contact_id,
               run_at,
               EXTRACT(EPOCH FROM (NOW() - run_at))::int AS lag_seconds
        FROM scheduled_jobs
        WHERE status = 'pending' AND run_at < NOW() - INTERVAL '10 minutes'
        ORDER BY run_at ASC
        LIMIT 50
    """)).fetchall()
    stuck_jobs = [
        {
            "job_id": r[0],
            "job_type": r[1],
            "contact_id": r[2],
            "run_at": r[3].isoformat() if r[3] else None,
            "lag_seconds": int(r[4]) if r[4] else 0,
        }
        for r in stuck_rows
    ]

    expired_rows = session.execute(text("""
        SELECT id, job_type, claimed_by,
               EXTRACT(EPOCH FROM (NOW() - lease_expires_at))::int AS age_seconds
        FROM scheduled_jobs
        WHERE status = 'running' AND lease_expires_at < NOW()
        ORDER BY lease_expires_at ASC
        LIMIT 50
    """)).fetchall()
    expired_leases = [
        {
            "job_id": r[0],
            "job_type": r[1],
            "worker_id": r[2],
            "age_seconds": int(r[3]) if r[3] else 0,
        }
        for r in expired_rows
    ]

    # ── CRM health ────────────────────────────────────────────────────────────
    task_row = session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'created') AS success,
            COUNT(*)                                    AS total
        FROM task_events
        WHERE created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()
    task_success = int(task_row[0]) if task_row else 0
    task_total = int(task_row[1]) if task_row else 0
    ghl_task_success_rate = (
        round(task_success / task_total, 4) if task_total else None
    )

    vm_update_row = session.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'completed') AS success,
            COUNT(*)                                      AS total
        FROM scheduled_jobs
        WHERE job_type = 'update_ghl_after_vm_message'
          AND created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()
    vm_success = int(vm_update_row[0]) if vm_update_row else 0
    vm_total = int(vm_update_row[1]) if vm_update_row else 0
    ghl_vm_update_success_rate = (
        round(vm_success / vm_total, 4) if vm_total else None
    )

    shadow_ghl_row = session.execute(text("""
        SELECT COUNT(*) FROM shadow_actions
        WHERE action_type = 'ghl_contact_update'
          AND created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()
    ghl_shadow_write_count = int(shadow_ghl_row[0]) if shadow_ghl_row else 0

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "campaign_filter": campaign,
        "kpis": {
            "total_calls": total,
            "pickup_rate": pickup_rate,
            "voicemail_rate": voicemail_rate,
            "failed_rate": failed_rate,
            "do_not_call_rate": do_not_call_rate,
            "enrolled_count": enrolled_count,
        },
        "ai": {
            "blank_transcript_rate": blank_transcript_rate,
            "intent_distribution": intent_distribution,
            "consent_distribution": consent_distribution,
        },
        "queue": {
            "stuck_jobs": stuck_jobs,
            "expired_leases": expired_leases,
        },
        "crm": {
            "ghl_task_success_rate": ghl_task_success_rate,
            "ghl_vm_update_success_rate": ghl_vm_update_success_rate,
            "ghl_shadow_write_count": ghl_shadow_write_count,
        },
    }


# ── Voice Performance ─────────────────────────────────────────────────────────

_VM_IN = "('voicemail','hangup_on_voicemail','left_voicemail','voicemail_detected','machine_detected')"


def _compute_voice_kpis(
    session: Session,
    from_dt: datetime,
    to_dt: datetime,
    days: float,
) -> dict[str, Any]:
    """Aggregate voice KPIs for a given time window."""
    row = session.execute(text(f"""
        SELECT
            COUNT(DISTINCT ce.contact_id)                                  AS unique_contacts,
            COUNT(*)                                                        AS total_calls,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                AS completed,
            COUNT(*) FILTER (WHERE ce.status IN {_VM_IN})                  AS voicemail,
            COUNT(*) FILTER (WHERE ce.status = 'failed')                   AS failed,
            COUNT(*) FILTER (WHERE ce.detected_intent = 'enrolled')        AS booked,
            AVG(COALESCE(ce.duration_seconds, 0))                          AS avg_duration
        FROM call_events ce
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchone()

    unique_contacts = int(row[0]) if row and row[0] else 0
    total_calls = int(row[1]) if row and row[1] else 0
    completed = int(row[2]) if row and row[2] else 0
    voicemail = int(row[3]) if row and row[3] else 0
    failed = int(row[4]) if row and row[4] else 0
    booked = int(row[5]) if row and row[5] else 0
    avg_duration = float(row[6]) if row and row[6] else 0.0

    safe_days = max(1.0, days)
    return {
        "unique_contacts": unique_contacts,
        "booked_appts": booked,
        "calls_per_day": round(total_calls / safe_days, 1),
        "completion_rate": round(completed / total_calls, 4) if total_calls else None,
        "avg_call_duration_sec": round(avg_duration, 1),
        "pickup_rate": round(completed / total_calls, 4) if total_calls else None,
        "voicemail_rate": round(voicemail / total_calls, 4) if total_calls else None,
        "failed_rate": round(failed / total_calls, 4) if total_calls else None,
        "total_calls": total_calls,
        "booking_rate": round(booked / unique_contacts, 4) if unique_contacts else None,
    }


def get_voice_performance(
    session: Session,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Voice Call Performance analytics — feeds /voice-performance dashboard page.

    Returns:
      period, kpis (current), kpis_prev (prior equal-length period),
      wow_changes (% delta per KPI), time_series (weekly buckets),
      campaign_breakdown (per-campaign scatter aggregates).
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    from_dt = from_date or (now - timedelta(days=28))

    days = max(1.0, (to_dt - from_dt).total_seconds() / 86400)

    # Prior period (same length, immediately before current window)
    prior_to = from_dt
    prior_from = prior_to - timedelta(days=days)

    kpis_curr = _compute_voice_kpis(session, from_dt, to_dt, days)
    kpis_prev = _compute_voice_kpis(session, prior_from, prior_to, days)

    # ── WoW % changes ─────────────────────────────────────────────────────────
    wow_keys = [
        "unique_contacts", "booked_appts", "total_calls", "completion_rate",
        "avg_call_duration_sec", "pickup_rate", "voicemail_rate", "failed_rate",
        "booking_rate",
    ]

    def _wow(curr: Any, prev: Any) -> float | None:
        if prev is None or prev == 0:
            return None
        if curr is None:
            return None
        return round((curr - prev) / abs(prev) * 100, 1)

    wow_changes = {k: _wow(kpis_curr.get(k), kpis_prev.get(k)) for k in wow_keys}

    # ── Weekly time series ────────────────────────────────────────────────────
    ts_rows = session.execute(text(f"""
        SELECT
            date_trunc('week', ce.created_at)                              AS week_start,
            COALESCE(ls.campaign_name, 'Unknown')                          AS campaign_name,
            COUNT(*)                                                        AS total_calls,
            COUNT(DISTINCT ce.contact_id)                                  AS unique_contacts,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                AS completed,
            COUNT(*) FILTER (WHERE ce.status IN {_VM_IN})                  AS voicemail,
            COUNT(*) FILTER (WHERE ce.status = 'failed')                   AS failed,
            COUNT(*) FILTER (WHERE ce.detected_intent = 'enrolled')        AS booked,
            AVG(COALESCE(ce.duration_seconds, 0))                          AS avg_duration
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
        GROUP BY date_trunc('week', ce.created_at), ls.campaign_name
        ORDER BY week_start ASC, ls.campaign_name
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    # Pivot by week
    from collections import defaultdict
    weeks: dict[str, dict[str, Any]] = {}
    for r in ts_rows:
        wk = r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10]
        camp = (r[1] or "Unknown").lower()
        t = int(r[2])
        u = int(r[3])
        c = int(r[4])
        v = int(r[5])
        f = int(r[6])
        b = int(r[7])
        d = float(r[8]) if r[8] else 0.0

        if wk not in weeks:
            weeks[wk] = {
                "cold": 0, "inbound": 0, "new_lead": 0,
                "total": 0, "completed": 0, "voicemail": 0, "failed": 0,
                "booked": 0, "unique": 0,
                "dur_sum": 0.0, "dur_count": 0,
            }
        w = weeks[wk]
        if "cold" in camp:
            w["cold"] += t
        elif "inbound" in camp:
            w["inbound"] += t
        elif "new" in camp:
            w["new_lead"] += t

        w["total"] += t
        w["unique"] += u
        w["completed"] += c
        w["voicemail"] += v
        w["failed"] += f
        w["booked"] += b
        w["dur_sum"] += d * t
        w["dur_count"] += t

    time_series = []
    for wk_date in sorted(weeks):
        w = weeks[wk_date]
        t = w["total"]
        u = w["unique"]
        time_series.append({
            "date": wk_date,
            "cold": w["cold"],
            "inbound": w["inbound"],
            "new_lead": w["new_lead"],
            "completion_rate": round(w["completed"] / t * 100, 1) if t else 0.0,
            "pickup_rate": round(w["completed"] / t * 100, 1) if t else 0.0,
            "voicemail_rate": round(w["voicemail"] / t * 100, 1) if t else 0.0,
            "failed_rate": round(w["failed"] / t * 100, 1) if t else 0.0,
            "booked_appts": w["booked"],
            "booking_rate": round(w["booked"] / u * 100, 1) if u else 0.0,
            "unique_contacts": u,
            "calls_per_day": round(t / 7, 1),
            "avg_call_duration_sec": round(w["dur_sum"] / w["dur_count"], 1) if w["dur_count"] else 0.0,
        })

    # ── Campaign breakdown for scatter ────────────────────────────────────────
    camp_rows = session.execute(text(f"""
        SELECT
            COALESCE(ls.campaign_name, 'Unknown')                          AS campaign_name,
            COUNT(*)                                                        AS total_calls,
            COUNT(DISTINCT ce.contact_id)                                  AS unique_contacts,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                AS completed,
            COUNT(*) FILTER (WHERE ce.detected_intent = 'enrolled')        AS booked
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
        GROUP BY ls.campaign_name
        ORDER BY total_calls DESC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    campaign_breakdown = []
    for r in camp_rows:
        t = int(r[1])
        u = int(r[2])
        c = int(r[3])
        b = int(r[4])
        campaign_breakdown.append({
            "campaign": r[0],
            "total_calls": t,
            "pickup_rate": round(c / t * 100, 1) if t else 0.0,
            "booking_rate": round(b / u * 100, 1) if u else 0.0,
            "avg_calls_per_day": round(t / max(1.0, days), 1),
        })

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "kpis": kpis_curr,
        "kpis_prev": kpis_prev,
        "wow_changes": wow_changes,
        "time_series": time_series,
        "campaign_breakdown": campaign_breakdown,
    }


def get_ai_timeseries(
    session: Session,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Weekly AI behavior time series — feeds the AI Performance trend charts.

    Returns per-week: total calls, blank transcript rate, unknown intent rate,
    and a full intent distribution dict.
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    from_dt = from_date or (now - timedelta(days=28))

    # Per-week aggregates
    rows = session.execute(text("""
        SELECT
            date_trunc('week', created_at)                                   AS week_start,
            COUNT(*)                                                          AS total_calls,
            COUNT(*) FILTER (WHERE transcript IS NULL OR transcript = '')    AS blank_count,
            COUNT(*) FILTER (
                WHERE detected_intent = 'low_confidence_audio'
                   OR detected_intent IS NULL
            )                                                                 AS unknown_count
        FROM call_events
        WHERE created_at BETWEEN :from_dt AND :to_dt
        GROUP BY date_trunc('week', created_at)
        ORDER BY week_start ASC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    # Per-week intent breakdown
    intent_rows = session.execute(text("""
        SELECT
            date_trunc('week', created_at) AS week_start,
            detected_intent,
            COUNT(*)                        AS cnt
        FROM call_events
        WHERE created_at BETWEEN :from_dt AND :to_dt
          AND detected_intent IS NOT NULL
        GROUP BY date_trunc('week', created_at), detected_intent
        ORDER BY week_start ASC, cnt DESC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    weekly_intents: dict[str, dict[str, int]] = {}
    for r in intent_rows:
        wk = r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10]
        if wk not in weekly_intents:
            weekly_intents[wk] = {}
        weekly_intents[wk][r[1]] = int(r[2])

    time_series = []
    for r in rows:
        wk = r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10]
        total = int(r[1]) if r[1] else 0
        blank = int(r[2]) if r[2] else 0
        unknown = int(r[3]) if r[3] else 0
        time_series.append({
            "date": wk,
            "total_calls": total,
            "blank_transcript_rate": round(blank / total * 100, 1) if total else None,
            "unknown_intent_rate": round(unknown / total * 100, 1) if total else None,
            "intent_distribution": weekly_intents.get(wk, {}),
        })

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "time_series": time_series,
    }
