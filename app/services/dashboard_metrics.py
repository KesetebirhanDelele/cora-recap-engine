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
    from app.core.mode_flags import get_mode_flags
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

    # Today's exception count (created since UTC midnight)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_exc_row = session.execute(text("""
        SELECT COUNT(*) FROM exceptions WHERE created_at >= :today_start
    """), {"today_start": today_start}).fetchone()
    today_exception_count = int(today_exc_row[0]) if today_exc_row else 0

    # Resolved in last 24h
    window_24h = now - timedelta(hours=24)
    resolved_row = session.execute(text("""
        SELECT COUNT(*) FROM exceptions
        WHERE status = 'resolved' AND updated_at >= :window
    """), {"window": window_24h}).fetchone()
    resolved_last_24h = int(resolved_row[0]) if resolved_row else 0

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
        "today_exception_count": today_exception_count,
        "resolved_last_24h": resolved_last_24h,
        "stuck_job_count": stuck_job_count,
        "expired_lease_count": expired_lease_count,
        "jobs_completed_last_5m": jobs_completed_5m,
        "jobs_failed_last_5m": jobs_failed_5m,
        "error_rate": error_rate,
        "shadow_mode_enabled": get_mode_flags(session, settings).shadow_mode_enabled,
        "ghl_write_mode": get_mode_flags(session, settings).ghl_write_mode,
        "app_env": settings.app_env,
        "recorded_at": now.isoformat(),
    }


def get_worker_activity(session: Session) -> dict[str, Any]:
    """
    Per-worker throughput and latency over the last 10 minutes.

    Returns every worker seen in a running state or that completed/failed a job
    in the last 10 minutes. Two queries:
      1. Per-worker summary: active flag, current job type, job count, avg duration.
      2. Per-worker per-job-type breakdown for the detail rows.
    """
    summary_rows = session.execute(text("""
        SELECT
            claimed_by,
            BOOL_OR(status = 'running' AND lease_expires_at > NOW())        AS is_active,
            MIN(CASE WHEN status = 'running'
                      AND lease_expires_at > NOW() THEN job_type END)        AS current_job_type,
            COUNT(*) FILTER (
                WHERE status IN ('completed','failed')
                  AND updated_at >= NOW() - INTERVAL '10 minutes'
            )                                                                AS jobs_last_10m,
            ROUND(AVG(
                GREATEST(EXTRACT(EPOCH FROM (updated_at - claimed_at)), 0)
            ) FILTER (
                WHERE status = 'completed'
                  AND updated_at >= NOW() - INTERVAL '10 minutes'
                  AND claimed_at IS NOT NULL
            )::numeric, 1)                                                   AS avg_duration_s
        FROM scheduled_jobs
        WHERE claimed_by IS NOT NULL
          AND claimed_by LIKE 'worker-%'
          AND (
              (status = 'running' AND lease_expires_at > NOW())
              OR (status IN ('completed','failed')
                  AND updated_at >= NOW() - INTERVAL '10 minutes')
          )
        GROUP BY claimed_by
        ORDER BY claimed_by
    """)).fetchall()

    breakdown_rows = session.execute(text("""
        SELECT
            claimed_by,
            job_type,
            COUNT(*)                                                          AS cnt,
            ROUND(AVG(
                GREATEST(EXTRACT(EPOCH FROM (updated_at - claimed_at)), 0)
            )::numeric, 1)                                                    AS avg_duration_s
        FROM scheduled_jobs
        WHERE status IN ('completed','failed')
          AND updated_at >= NOW() - INTERVAL '10 minutes'
          AND claimed_by IS NOT NULL
          AND claimed_by LIKE 'worker-%'
          AND claimed_at IS NOT NULL
        GROUP BY claimed_by, job_type
        ORDER BY claimed_by, cnt DESC
    """)).fetchall()

    # Index breakdown by worker
    breakdown_by_worker: dict[str, list] = {}
    for r in breakdown_rows:
        worker_id = r[0]
        breakdown_by_worker.setdefault(worker_id, []).append({
            "job_type": r[1],
            "count": int(r[2]),
            "avg_duration_s": float(r[3]) if r[3] is not None else None,
        })

    now = datetime.now(tz=timezone.utc)
    workers = []
    for r in summary_rows:
        worker_id = r[0]
        # worker_id format: worker-{role}-{host6}  (e.g. worker-default-abc123)
        # Use as-is for display — it's already compact and human-readable.
        workers.append({
            "worker_id": worker_id,
            "worker_id_short": worker_id,
            "is_active": bool(r[1]),
            "current_job_type": r[2],
            "jobs_last_10m": int(r[3]) if r[3] is not None else 0,
            "avg_duration_s": float(r[4]) if r[4] is not None else None,
            "breakdown": breakdown_by_worker.get(worker_id, []),
        })

    return {
        "workers": workers,
        "window_minutes": 10,
        "recorded_at": now.isoformat(),
    }


def get_worker_activity_trend(session: Session) -> dict[str, Any]:
    """
    6-hour per-worker trend in 10-minute buckets.

    Uses PERCENTILE_CONT(0.5) (median) for duration — not average — so a single
    stuck job does not skew the reported duration for an entire bucket.

    Returns flat points list + a deduped worker_ids index. The frontend pivots
    points into wide format for recharts stacked-area and multi-line charts.
    """
    rows = session.execute(text("""
        SELECT
            claimed_by,
            date_trunc('hour', updated_at)
                + (EXTRACT(MINUTE FROM updated_at)::int / 10) * INTERVAL '10 minutes'
                                                                             AS bucket,
            COUNT(*)                                                          AS jobs,
            ROUND(
                (PERCENTILE_CONT(0.5) WITHIN GROUP (
                    ORDER BY GREATEST(EXTRACT(EPOCH FROM (updated_at - claimed_at)), 0)
                ) FILTER (WHERE claimed_at IS NOT NULL))::numeric,
            1)                                                                AS median_duration_s
        FROM scheduled_jobs
        WHERE claimed_by IS NOT NULL
          AND claimed_by LIKE 'worker-%'
          AND updated_at >= NOW() - INTERVAL '6 hours'
          AND status IN ('completed', 'failed')
        GROUP BY
            claimed_by,
            date_trunc('hour', updated_at)
                + (EXTRACT(MINUTE FROM updated_at)::int / 10) * INTERVAL '10 minutes'
        ORDER BY bucket, claimed_by
    """)).fetchall()

    seen_workers: dict[str, str] = {}
    for r in rows:
        worker_id = r[0]
        if worker_id not in seen_workers:
            # worker_id format: worker-{role}-{host6} — use as-is for display.
            seen_workers[worker_id] = worker_id

    points = [
        {
            "bucket": r[1].isoformat() if r[1] else None,
            "worker_id": r[0],
            "worker_id_short": seen_workers[r[0]],
            "jobs": int(r[2]),
            "median_duration_s": float(r[3]) if r[3] is not None else None,
        }
        for r in rows
    ]

    now = datetime.now(tz=timezone.utc)
    return {
        "points": points,
        "worker_ids": [
            {"worker_id": wid, "worker_id_short": short}
            for wid, short in seen_workers.items()
        ],
        "window_minutes": 360,
        "bucket_minutes": 10,
        "recorded_at": now.isoformat(),
    }


def get_metrics(
    session: Session,
    campaign: str | None = None,
    direction: str | None = None,
    voice_agent: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Compute aggregated KPIs, queue detail, and CRM metrics for a time window.
    Returns a dict matching the GET /dashboard/metrics response schema.

    campaign:    "New Lead" | "Cold Lead" | "Inbound" (business campaigns)
    direction:   "Outbound" | "Inbound" (case-insensitive match on call_events.direction)
    voice_agent: "ColdLead" | "NewLead" | "Inbound" (call_events.voice_agent)
    """
    now = datetime.now(tz=timezone.utc)
    from_dt = from_date or (now - timedelta(days=7))
    to_dt = to_date or now

    params: dict[str, Any] = {"from_dt": from_dt, "to_dt": to_dt}

    # Campaign filter — "Inbound", "New Lead", or "Cold Lead"
    campaign_filter = ""
    needs_ls_join = False
    if campaign == "Inbound":
        campaign_filter = "AND ce.campaign_name = 'Inbound'"
    elif campaign:
        campaign_filter = "AND ls.campaign_name = :campaign"
        params["campaign"] = campaign
        needs_ls_join = True

    # Direction filter:
    #   "Inbound"  → exact match (LOWER)
    #   "Outbound" → everything that is NOT inbound (handles NULLs and other variants)
    direction_filter = ""
    if direction:
        if direction.lower() == "inbound":
            direction_filter = "AND LOWER(ce.direction) = 'inbound'"
        else:
            direction_filter = "AND (ce.direction IS NULL OR LOWER(ce.direction) != 'inbound')"

    # Voice agent filter — exact match on ce.voice_agent
    agent_filter = ""
    if voice_agent:
        agent_filter = "AND ce.voice_agent = :voice_agent"
        params["voice_agent"] = voice_agent

    # Join lead_state whenever campaign filter needs it
    ls_join = "LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id" if needs_ls_join else ""

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
        {direction_filter}
        {agent_filter}
    """
    kpi_row = session.execute(text(kpi_sql), params).fetchone()
    total = int(kpi_row[0]) if kpi_row else 0
    completed_n = int(kpi_row[1]) if kpi_row else 0
    voicemail_n = int(kpi_row[2]) if kpi_row else 0
    failed_n = int(kpi_row[3]) if kpi_row else 0

    pickup_rate = round(completed_n / total, 4) if total else None
    voicemail_rate = round(voicemail_n / total, 4) if total else None
    failed_rate = round(failed_n / total, 4) if total else None

    enrolled_sql = f"""
        SELECT COUNT(*) FROM call_events ce
        {ls_join}
        WHERE ce.detected_intent = 'enrolled'
          AND ce.created_at BETWEEN :from_dt AND :to_dt
        {campaign_filter}
        {direction_filter}
        {agent_filter}
    """
    enrolled_row = session.execute(text(enrolled_sql), params).fetchone()
    enrolled_count = int(enrolled_row[0]) if enrolled_row else 0

    dnc_row = session.execute(text("""
        SELECT COUNT(*) FROM lead_state WHERE do_not_call = true
    """)).fetchone()
    do_not_call_count = int(dnc_row[0]) if dnc_row else 0
    do_not_call_rate = (
        round(do_not_call_count / total, 4) if total else None
    )

    # ── AI metrics ────────────────────────────────────────────────────────────
    blank_sql = f"""
        SELECT COUNT(*) FROM call_events ce
        {ls_join}
        WHERE (ce.transcript IS NULL OR ce.transcript = '')
          AND ce.created_at BETWEEN :from_dt AND :to_dt
        {campaign_filter}
        {direction_filter}
        {agent_filter}
    """
    blank_row = session.execute(text(blank_sql), params).fetchone()
    blank_n = int(blank_row[0]) if blank_row else 0
    blank_transcript_rate = round(blank_n / total, 4) if total else None

    intent_sql = f"""
        SELECT ce.detected_intent, COUNT(*) AS cnt
        FROM call_events ce
        {ls_join}
        WHERE ce.detected_intent IS NOT NULL
          AND ce.created_at BETWEEN :from_dt AND :to_dt
        {campaign_filter}
        {direction_filter}
        {agent_filter}
        GROUP BY ce.detected_intent
        ORDER BY cnt DESC
    """
    intent_rows = session.execute(text(intent_sql), params).fetchall()
    intent_distribution = {r[0]: int(r[1]) for r in intent_rows}

    consent_sql = f"""
        SELECT sr.summary_consent, COUNT(*) AS cnt
        FROM summary_results sr
        JOIN call_events ce ON ce.id = sr.call_event_id
        {ls_join}
        WHERE sr.created_at BETWEEN :from_dt AND :to_dt
        {campaign_filter}
        {direction_filter}
        {agent_filter}
        GROUP BY sr.summary_consent
    """
    consent_rows = session.execute(text(consent_sql), params).fetchall()
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

# Booking signal: action_ghl_create_booking fired in executed_actions.
_BOOKED_COND = """(
    ce.raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
)"""

# Unique contact dedup: inbound callers are identified by phone_number_from (the number
# they called from); outbound leads are identified by phone_number_to (the number dialled).
_UNIQUE_PHONE = """CASE
    WHEN lower(ce.direction) = 'inbound'
    THEN ce.raw_payload_json->>'phone_number_from'
    ELSE ce.raw_payload_json->>'phone_number_to'
END"""


def _compute_voice_kpis(
    session: Session,
    from_dt: datetime,
    to_dt: datetime,
    days: float,
) -> dict[str, Any]:
    """Aggregate voice KPIs for a given time window."""
    row = session.execute(text(f"""
        SELECT
            COUNT(DISTINCT {_UNIQUE_PHONE})                                 AS unique_contacts,
            COUNT(*)                                                        AS total_calls,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                AS completed,
            COUNT(*) FILTER (WHERE ce.status IN {_VM_IN})                  AS voicemail,
            COUNT(*) FILTER (WHERE ce.status = 'failed')                   AS failed,
            COUNT(*) FILTER (WHERE {_BOOKED_COND})                         AS booked,
            AVG(COALESCE(ce.duration_seconds, 0))                          AS avg_duration
        FROM call_events ce
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
          AND NOT ce.report_excluded
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
    all_time: bool = False,
    wow_mode: bool = False,
    wow_shift: bool = False,
) -> dict[str, Any]:
    """
    Voice Call Performance analytics — feeds /voice-performance dashboard page.

    Returns:
      period, kpis (current), kpis_prev (prior equal-length period),
      wow_changes (% delta per KPI), time_series (weekly buckets),
      campaign_breakdown (per-campaign scatter aggregates).

    all_time=True: skips the default 28-day floor and queries from the earliest
    record, so KPIs and campaign_breakdown reflect cumulative totals.

    wow_mode=True: wow_changes is computed as this calendar week (Mon–now) vs
    last calendar week (Mon–Sun, complete), regardless of from_date/to_date.
    All other response fields still reflect the requested date window.
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    if all_time and from_date is None:
        # Find the earliest call_event timestamp so the window covers all records
        from sqlalchemy import text as _text
        row = session.execute(_text("SELECT MIN(created_at) FROM call_events")).fetchone()
        earliest = row[0] if (row and row[0]) else None
        if earliest is not None and earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=timezone.utc)
        from_dt = earliest or (now - timedelta(days=365))
    else:
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

    if wow_mode:
        # Calendar-week WoW: week containing to_dt (Mon 00:00 → to_dt) vs prior full week.
        # When to_dt == now (default, no to_date supplied) this is identical to the old behaviour.
        ref = to_dt
        weekday = ref.weekday()  # Mon=0 … Sun=6
        this_week_start = (ref - timedelta(days=weekday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        last_week_start = this_week_start - timedelta(days=7)
        days_this_week = max(1.0, (ref - this_week_start).total_seconds() / 86400)
        wow_curr = _compute_voice_kpis(session, this_week_start, ref, days_this_week)
        wow_prev = _compute_voice_kpis(session, last_week_start, this_week_start, 7.0)
        wow_changes = {k: _wow(wow_curr.get(k), wow_prev.get(k)) for k in wow_keys}

        # Count-based KPIs use cumulative formula:
        # WoW% = (total_till_ref - total_till_end_of_last_week) / total_till_end_of_last_week
        cum_row = session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE
                    raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
                ) AS booked_now,
                COUNT(*) FILTER (WHERE
                    raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
                    AND created_at < :week_start
                ) AS booked_prev,
                COUNT(DISTINCT {_UNIQUE_PHONE}) AS unique_now,
                COUNT(DISTINCT {_UNIQUE_PHONE}) FILTER (WHERE created_at < :week_start) AS unique_prev
            FROM call_events ce
            WHERE NOT ce.report_excluded
              AND created_at <= :ref_dt
        """), {"week_start": this_week_start, "ref_dt": ref}).fetchone()
        if cum_row:
            wow_changes["booked_appts"]     = _wow(cum_row[0], cum_row[1])
            wow_changes["unique_contacts"]  = _wow(cum_row[2], cum_row[3])
    elif wow_shift:
        # Rate-based KPIs: shift the ENTIRE filter range back 7 days.
        # Count-based KPIs (unique_contacts, booked_appts): use calendar-week cumulative
        # formula — COUNT DISTINCT is not meaningful when two large overlapping windows
        # differ by only 7 days at each boundary.
        shift = timedelta(days=7)
        kpis_shifted = _compute_voice_kpis(session, from_dt - shift, to_dt - shift, days)
        wow_changes = {k: _wow(kpis_curr.get(k), kpis_shifted.get(k)) for k in wow_keys}

        # Override unique_contacts and booked_appts with calendar-week cumulative formula
        # (same as wow_mode): new contacts/bookings this week vs total before this week.
        ref = to_dt
        weekday = ref.weekday()
        this_week_start = (ref - timedelta(days=weekday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        cum_row = session.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE
                    raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
                ) AS booked_now,
                COUNT(*) FILTER (WHERE
                    raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
                    AND created_at < :week_start
                ) AS booked_prev,
                COUNT(DISTINCT {_UNIQUE_PHONE}) AS unique_now,
                COUNT(DISTINCT {_UNIQUE_PHONE}) FILTER (WHERE created_at < :week_start) AS unique_prev
            FROM call_events ce
            WHERE NOT ce.report_excluded
              AND created_at <= :ref_dt
        """), {"week_start": this_week_start, "ref_dt": ref}).fetchone()
        if cum_row:
            wow_changes["booked_appts"]    = _wow(cum_row[0], cum_row[1])
            wow_changes["unique_contacts"] = _wow(cum_row[2], cum_row[3])
    else:
        wow_changes = {k: _wow(kpis_curr.get(k), kpis_prev.get(k)) for k in wow_keys}

    # ── Weekly time series ────────────────────────────────────────────────────
    # Group by voice_agent (ColdLead | NewLead | Inbound) — per-call attribute.
    ts_rows = session.execute(text(f"""
        SELECT
            date_trunc('week', COALESCE(ce.call_started_at, ce.created_at))   AS week_start,
            ce.voice_agent                                                     AS voice_agent,
            COUNT(*)                                                           AS total_calls,
            COUNT(DISTINCT {_UNIQUE_PHONE})                                    AS unique_contacts,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                   AS completed,
            COUNT(*) FILTER (WHERE ce.status IN {_VM_IN})                     AS voicemail,
            COUNT(*) FILTER (WHERE ce.status = 'failed')                      AS failed,
            COUNT(*) FILTER (WHERE {_BOOKED_COND})                            AS booked,
            AVG(COALESCE(ce.duration_seconds, 0))                             AS avg_duration
        FROM call_events ce
        WHERE COALESCE(ce.call_started_at, ce.created_at) >= date_trunc('week', CAST(:from_dt AS timestamptz))
          AND COALESCE(ce.call_started_at, ce.created_at) <= :to_dt
          AND NOT ce.report_excluded
        GROUP BY date_trunc('week', COALESCE(ce.call_started_at, ce.created_at)), ce.voice_agent
        ORDER BY week_start ASC, ce.voice_agent
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    # Per-week true unique-contact count — NOT grouped by voice_agent to avoid
    # double-counting contacts who received calls from multiple campaign types.
    ts_unique_rows = session.execute(text(f"""
        SELECT
            date_trunc('week', COALESCE(ce.call_started_at, ce.created_at))   AS week_start,
            COUNT(DISTINCT {_UNIQUE_PHONE})                                    AS unique_contacts
        FROM call_events ce
        WHERE COALESCE(ce.call_started_at, ce.created_at) >= date_trunc('week', CAST(:from_dt AS timestamptz))
          AND COALESCE(ce.call_started_at, ce.created_at) <= :to_dt
          AND NOT ce.report_excluded
        GROUP BY date_trunc('week', COALESCE(ce.call_started_at, ce.created_at))
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()
    # Build a week → true unique count lookup
    week_unique: dict[str, int] = {}
    for r in ts_unique_rows:
        wk = r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10]
        week_unique[wk] = int(r[1]) if r[1] else 0

    # Pivot by week — keyed by voice_agent value
    weeks: dict[str, dict[str, Any]] = {}
    for r in ts_rows:
        wk = r[0].date().isoformat() if hasattr(r[0], "date") else str(r[0])[:10]
        agent = (r[1] or "").lower()  # "coldlead" | "newlead" | "inbound" | ""
        t = int(r[2])
        u = int(r[3])
        c = int(r[4])
        v = int(r[5])
        f = int(r[6])
        b = int(r[7])
        d = float(r[8]) if r[8] else 0.0

        if wk not in weeks:
            def _empty_camp() -> dict[str, Any]:
                return {"t": 0, "c": 0, "v": 0, "f": 0, "b": 0, "u": 0, "dur_sum": 0.0}
            weeks[wk] = {
                "cold": 0, "inbound": 0, "new_lead": 0,
                "total": 0, "completed": 0, "voicemail": 0, "failed": 0,
                "booked": 0, "unique": 0,
                "dur_sum": 0.0, "dur_count": 0,
                "cold_s":    _empty_camp(),
                "inbound_s": _empty_camp(),
                "new_lead_s":_empty_camp(),
            }
        w = weeks[wk]

        camp_key: str | None = None
        if agent == "coldlead":
            w["cold"] += t
            camp_key = "cold_s"
        elif agent == "inbound":
            w["inbound"] += t
            camp_key = "inbound_s"
        elif agent == "newlead":
            w["new_lead"] += t
            camp_key = "new_lead_s"

        w["total"] += t
        # w["unique"] is set from the deduplicated per-week query below; do not
        # accumulate per-agent unique counts here (would double-count contacts
        # who received calls from more than one campaign type in the same week).
        w["completed"] += c
        w["voicemail"] += v
        w["failed"] += f
        w["booked"] += b
        w["dur_sum"] += d * t
        w["dur_count"] += t

        if camp_key:
            cs = w[camp_key]
            cs["t"] += t
            cs["c"] += c
            cs["v"] += v
            cs["f"] += f
            cs["b"] += b
            cs["u"] += u
            cs["dur_sum"] += d * t

    def _camp_stats(cs: dict[str, Any]) -> dict[str, Any]:
        """Compute derived rates from a per-campaign accumulator."""
        ct = cs["t"]
        cu = cs["u"]
        return {
            "total_calls":         ct,
            "unique_contacts":     cu,
            "booked_appts":        cs["b"],
            "calls_per_day":       round(ct / 7, 1),
            "completion_rate":     round(cs["c"] / ct * 100, 1) if ct else None,
            "pickup_rate":         round(cs["c"] / ct * 100, 1) if ct else None,
            "voicemail_rate":      round(cs["v"] / ct * 100, 1) if ct else None,
            "failed_rate":         round(cs["f"] / ct * 100, 1) if ct else None,
            "booking_rate":        round(cs["b"] / cu * 100, 1) if cu else None,
            "avg_call_duration_sec": round(cs["dur_sum"] / ct, 1) if ct else 0.0,
        }

    time_series = []
    for wk_date in sorted(weeks):
        w = weeks[wk_date]
        t = w["total"]
        # Use the true per-week unique count (from the deduplicated query) so
        # contacts who appeared in multiple campaign types are not double-counted.
        u = week_unique.get(wk_date, w["unique"])
        time_series.append({
            "date": wk_date,
            "cold": w["cold"],
            "inbound": w["inbound"],
            "new_lead": w["new_lead"],
            "cold_unique": w["cold_s"]["u"],
            "inbound_unique": w["inbound_s"]["u"],
            "new_lead_unique": w["new_lead_s"]["u"],
            "completion_rate": round(w["completed"] / t * 100, 1) if t else 0.0,
            "pickup_rate": round(w["completed"] / t * 100, 1) if t else 0.0,
            "voicemail_rate": round(w["voicemail"] / t * 100, 1) if t else 0.0,
            "failed_rate": round(w["failed"] / t * 100, 1) if t else 0.0,
            "booked_appts": w["booked"],
            "booking_rate": round(w["booked"] / u * 100, 1) if u else 0.0,
            "unique_contacts": u,
            "calls_per_day": round(t / 7, 1),
            "avg_call_duration_sec": round(w["dur_sum"] / w["dur_count"], 1) if w["dur_count"] else 0.0,
            # Per-campaign breakdowns — consumed by tooltip when hovering a bar segment
            "cold_stats":    _camp_stats(w["cold_s"]),
            "inbound_stats": _camp_stats(w["inbound_s"]),
            "new_lead_stats":_camp_stats(w["new_lead_s"]),
        })

    # ── Voice agent breakdown for scatter ─────────────────────────────────────
    # Grouped by voice_agent (ColdLead | NewLead | Inbound) — per-call attribute.
    # Pickup-rate stats (status-filtered) joined to booking count (status-agnostic).
    # Booking signal fires on NULL-status Inbound records — must be counted separately.
    camp_rows = session.execute(text(f"""
        SELECT
            ce.voice_agent,
            COUNT(*)                                                        AS total_calls,
            COUNT(DISTINCT {_UNIQUE_PHONE})                                 AS unique_contacts,
            COUNT(*) FILTER (WHERE ce.status = 'completed')                AS completed,
            MAX(COALESCE(bk.booked, 0))                                    AS booked
        FROM call_events ce
        LEFT JOIN (
            SELECT voice_agent, COUNT(*) AS booked
            FROM call_events
            WHERE NOT report_excluded
              AND COALESCE(call_started_at, created_at)
                      >= date_trunc('week', CAST(:from_dt AS timestamptz))
              AND COALESCE(call_started_at, created_at) <= :to_dt
              AND voice_agent IS NOT NULL
              AND raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
            GROUP BY voice_agent
        ) bk ON bk.voice_agent = ce.voice_agent
        WHERE COALESCE(ce.call_started_at, ce.created_at)
                  >= date_trunc('week', CAST(:from_dt AS timestamptz))
          AND COALESCE(ce.call_started_at, ce.created_at) <= :to_dt
          AND ce.voice_agent IS NOT NULL
          AND NOT ce.report_excluded
          AND ce.status IS NOT NULL
          AND ce.status <> ''
        GROUP BY ce.voice_agent
        ORDER BY total_calls DESC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    campaign_breakdown = []
    for r in camp_rows:
        t = int(r[1])
        u = int(r[2])
        c = int(r[3])
        b = int(r[4])
        avg_cpd = round(t / max(1.0, days), 1)
        campaign_breakdown.append({
            "campaign": r[0],
            "total_calls": t,
            "pickup_rate": round(c / t * 100, 1) if t else 0.0,
            "booking_rate": round(b / u * 100, 1) if u else 0.0,
            "avg_calls_per_day": avg_cpd,
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
          AND NOT report_excluded
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
          AND NOT report_excluded
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


# ── Exception Trend ───────────────────────────────────────────────────────────

def get_exception_trend(
    session: Session,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
) -> dict[str, Any]:
    """
    Daily exception counts grouped by type.
    Returns a list of {date, type, count} points for the Exceptions Monitor trend chart.
    Default window: last 30 days.
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    from_dt = from_date or (now - timedelta(days=30))

    rows = session.execute(text("""
        SELECT
            date_trunc('day', created_at)::date AS day,
            type,
            COUNT(*) AS cnt
        FROM exceptions
        WHERE created_at BETWEEN :from_dt AND :to_dt
        GROUP BY date_trunc('day', created_at)::date, type
        ORDER BY day ASC, cnt DESC
    """), {"from_dt": from_dt, "to_dt": to_dt}).fetchall()

    points = [
        {
            "date": str(r[0]),
            "type": r[1],
            "count": int(r[2]),
        }
        for r in rows
    ]

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "points": points,
    }


# ── Exception Anomalies ───────────────────────────────────────────────────────

def get_exception_anomalies(session: Session) -> dict[str, Any]:
    """
    Detect patterns, spikes, and abnormal behavior in exception data.

    Returns:
      spikes       — types where last-24h count > 2x the prior-7-day daily average
      recurring    — top exception types by frequency (last 30 days)
      clusters     — contacts/entities with the most repeated failures (last 7 days)
      trend        — daily total exception count for the last 14 days (anomaly frequency)
    """
    now = datetime.now(tz=timezone.utc)
    window_24h = now - timedelta(hours=24)
    window_7d = now - timedelta(days=7)
    window_30d = now - timedelta(days=30)
    window_14d = now - timedelta(days=14)

    # ── Spike detection: last 24h vs prior 7-day average ─────────────────────
    recent_rows = session.execute(text("""
        SELECT type, COUNT(*) AS cnt
        FROM exceptions
        WHERE created_at >= :since
        GROUP BY type
    """), {"since": window_24h}).fetchall()
    recent_counts = {r[0]: int(r[1]) for r in recent_rows}

    baseline_rows = session.execute(text("""
        SELECT type, COUNT(*) AS cnt
        FROM exceptions
        WHERE created_at BETWEEN :from_dt AND :to_dt
        GROUP BY type
    """), {"from_dt": window_7d - timedelta(days=7), "to_dt": window_7d}).fetchall()
    # Daily average over prior 7-day window
    baseline_daily = {r[0]: int(r[1]) / 7.0 for r in baseline_rows}

    spikes = []
    for exc_type, recent_cnt in sorted(recent_counts.items(), key=lambda x: -x[1]):
        baseline = baseline_daily.get(exc_type, 0)
        if baseline == 0:
            # No prior history — flag if > 3 occurrences in 24h
            if recent_cnt >= 3:
                spikes.append({
                    "type": exc_type,
                    "recent_24h": recent_cnt,
                    "baseline_daily_avg": 0.0,
                    "spike_factor": None,
                    "is_new_type": True,
                })
        elif recent_cnt > 2 * baseline:
            spikes.append({
                "type": exc_type,
                "recent_24h": recent_cnt,
                "baseline_daily_avg": round(baseline, 2),
                "spike_factor": round(recent_cnt / baseline, 1),
                "is_new_type": False,
            })

    # ── Recurring issues: top types by total count (last 30 days) ────────────
    recurring_rows = session.execute(text("""
        SELECT type, severity, COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'open') AS open_cnt,
               COUNT(*) FILTER (WHERE status = 'resolved') AS resolved_cnt,
               MIN(created_at) AS first_seen,
               MAX(created_at) AS last_seen
        FROM exceptions
        WHERE created_at >= :since
        GROUP BY type, severity
        ORDER BY total DESC
        LIMIT 20
    """), {"since": window_30d}).fetchall()

    recurring = [
        {
            "type": r[0],
            "severity": r[1],
            "total": int(r[2]),
            "open": int(r[3]),
            "resolved": int(r[4]),
            "first_seen": r[5].isoformat() if r[5] and hasattr(r[5], "isoformat") else str(r[5]),
            "last_seen": r[6].isoformat() if r[6] and hasattr(r[6], "isoformat") else str(r[6]),
        }
        for r in recurring_rows
    ]

    # ── Failure clusters: contacts with most repeated failures (last 7 days) ──
    cluster_rows = session.execute(text("""
        SELECT entity_id, entity_type,
               COUNT(*) AS failure_count,
               array_agg(DISTINCT type ORDER BY type) AS exception_types,
               MAX(created_at) AS last_failure
        FROM exceptions
        WHERE created_at >= :since
          AND entity_id IS NOT NULL
        GROUP BY entity_id, entity_type
        HAVING COUNT(*) >= 2
        ORDER BY failure_count DESC
        LIMIT 15
    """), {"since": window_7d}).fetchall()

    clusters = [
        {
            "entity_id": r[0],
            "entity_type": r[1],
            "failure_count": int(r[2]),
            "exception_types": list(r[3]) if r[3] else [],
            "last_failure": r[4].isoformat() if r[4] and hasattr(r[4], "isoformat") else str(r[4]),
        }
        for r in cluster_rows
    ]

    # ── Anomaly trend: daily total exception count (last 14 days) ────────────
    trend_rows = session.execute(text("""
        SELECT date_trunc('day', created_at)::date AS day, COUNT(*) AS cnt
        FROM exceptions
        WHERE created_at >= :since
        GROUP BY date_trunc('day', created_at)::date
        ORDER BY day ASC
    """), {"since": window_14d}).fetchall()

    anomaly_trend = [
        {"date": str(r[0]), "count": int(r[1])}
        for r in trend_rows
    ]

    return {
        "spikes": spikes,
        "recurring": recurring,
        "clusters": clusters,
        "anomaly_trend": anomaly_trend,
        "computed_at": now.isoformat(),
    }


# ── Card Metrics (nav dashboard) ──────────────────────────────────────────────

def get_card_metrics(session: Session) -> dict[str, Any]:
    """
    Returns current + previous-period values for all navigation card indicators.

    Window:
      current  — last 24 hours
      previous — 24–48 hours ago

    Compact format:  {"value": X, "previous_value": Y}

    Metrics:
      events_per_min             — event_stream activity rate (last 5 min)
      open_exceptions            — exceptions WHERE status='open'
      backlog_size               — stuck + expired jobs
      active_alerts              — alert_events WHERE status='active'
      lookup_rate                — call_events in last hour (system activity proxy)
      config_health              — "healthy" | "warning" | "error"
      pickup_rate                — completed / total calls
      meaningful_engagement_rate — strong-intent calls / total calls
      booking_rate               — enrolled / unique contacts
      active_leads               — lead_state not closed/dnc
      in_vm_sequence             — leads in active VM tier (tier 0–2, not finalized)
      finalized_today            — leads finalized since midnight CST (closed/terminal/dnc)
      sync_success_rate          — task_events created / total
      anomaly_count              — exception types with spike (≥3 occurrences) in 24h
      urgent_leads_count         — calls in last 7 days with high-intent detected_intent
    """
    from app.config import get_settings

    now = datetime.now(tz=timezone.utc)
    w24_start = now - timedelta(hours=24)
    w48_start = now - timedelta(hours=48)
    w5m_start = now - timedelta(minutes=5)
    w10m_start = now - timedelta(minutes=10)
    w1h_start = now - timedelta(hours=1)
    w2h_start = now - timedelta(hours=2)
    w7d_start = now - timedelta(days=7)
    w14d_start = now - timedelta(days=14)
    # Calendar-week anchors (Mon 00:00 UTC)
    this_week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_start = this_week_start - timedelta(days=7)
    last_week_end   = this_week_start  # exclusive upper bound

    def _scalar(sql: str, params: dict | None = None) -> Any:
        return session.execute(text(sql), params or {}).scalar()

    def _r(v: Any) -> float | None:
        return round(float(v), 4) if v is not None else None

    # ── events_per_min ────────────────────────────────────────────────────────
    ev_curr = _scalar("SELECT COUNT(*) FROM event_stream WHERE created_at >= :w", {"w": w5m_start}) or 0
    ev_prev = _scalar("SELECT COUNT(*) FROM event_stream WHERE created_at BETWEEN :a AND :b", {"a": w10m_start, "b": w5m_start}) or 0

    # ── open_exceptions ───────────────────────────────────────────────────────
    open_exc = _scalar("SELECT COUNT(*) FROM exceptions WHERE status = 'open'") or 0
    prev_open_exc = _scalar(
        "SELECT COUNT(*) FROM exceptions WHERE status = 'open' AND created_at < :d",
        {"d": w24_start},
    ) or 0

    # ── backlog_size (stuck + expired leases) ─────────────────────────────────
    stuck = _scalar("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'pending' AND run_at < NOW() - INTERVAL '10 minutes'") or 0
    expired = _scalar("SELECT COUNT(*) FROM scheduled_jobs WHERE status = 'running' AND lease_expires_at < NOW()") or 0
    backlog = stuck + expired

    # ── active_alerts ─────────────────────────────────────────────────────────
    active_alerts = _scalar("SELECT COUNT(*) FROM alert_events WHERE status = 'active'") or 0

    # ── lookup_rate (call_events last hour as activity proxy) ─────────────────
    lookup_curr = _scalar("SELECT COUNT(*) FROM call_events WHERE created_at >= :w", {"w": w1h_start}) or 0
    lookup_prev = _scalar(
        "SELECT COUNT(*) FROM call_events WHERE created_at BETWEEN :a AND :b",
        {"a": w2h_start, "b": w1h_start},
    ) or 0

    # ── config_health ─────────────────────────────────────────────────────────
    try:
        settings = get_settings()
        config_ok = bool(
            getattr(settings, "ghl_api_key", None)
            and getattr(settings, "openai_api_key", None)
        )
        config_health = "healthy" if config_ok else "warning"
    except Exception:
        config_health = "error"

    # ── calls_today (resets at midnight America/Chicago) ─────────────────────
    _midnight_cst = (
        "DATE_TRUNC('day', NOW() AT TIME ZONE 'America/Chicago')"
        " AT TIME ZONE 'America/Chicago'"
    )
    _yesterday_start = (
        "(DATE_TRUNC('day', NOW() AT TIME ZONE 'America/Chicago') - INTERVAL '1 day')"
        " AT TIME ZONE 'America/Chicago'"
    )
    calls_today = int(_scalar(
        f"SELECT COUNT(*) FROM call_events"
        f" WHERE COALESCE(call_started_at, created_at) >= {_midnight_cst}"
    ) or 0)
    calls_yesterday = int(_scalar(
        f"SELECT COUNT(*) FROM call_events"
        f" WHERE COALESCE(call_started_at, created_at) >= {_yesterday_start}"
        f"   AND COALESCE(call_started_at, created_at) < {_midnight_cst}"
    ) or 0)

    # ── pickup_rate ───────────────────────────────────────────────────────────
    pickup_curr = _r(_scalar(
        "SELECT COUNT(*) FILTER (WHERE status = 'completed')::float / NULLIF(COUNT(*), 0) FROM call_events WHERE created_at >= :s",
        {"s": w24_start},
    ))
    pickup_prev = _r(_scalar(
        "SELECT COUNT(*) FILTER (WHERE status = 'completed')::float / NULLIF(COUNT(*), 0) FROM call_events WHERE created_at BETWEEN :a AND :b",
        {"a": w48_start, "b": w24_start},
    ))

    # ── meaningful_engagement_rate ────────────────────────────────────────────
    _strong = "('enrolled','callback_request','re_engaged','interested_not_now')"
    mer_curr = _r(_scalar(
        f"SELECT COUNT(*) FILTER (WHERE detected_intent IN {_strong})::float / NULLIF(COUNT(*), 0) FROM call_events WHERE created_at >= :s",
        {"s": w24_start},
    ))
    mer_prev = _r(_scalar(
        f"SELECT COUNT(*) FILTER (WHERE detected_intent IN {_strong})::float / NULLIF(COUNT(*), 0) FROM call_events WHERE created_at BETWEEN :a AND :b",
        {"a": w48_start, "b": w24_start},
    ))

    # ── booking_rate WoW: this calendar week (Mon–now) vs last full week (Mon–Sun) ──
    book_curr = _r(_scalar(
        """SELECT COUNT(*) FILTER (WHERE
                raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
           )::float / NULLIF(COUNT(DISTINCT CASE
                WHEN lower(direction) = 'inbound' THEN raw_payload_json->>'phone_number_from'
                ELSE raw_payload_json->>'phone_number_to'
           END), 0)
           FROM call_events WHERE created_at >= :s""",
        {"s": this_week_start},
    ))
    book_prev = _r(_scalar(
        """SELECT COUNT(*) FILTER (WHERE
                raw_payload_json->>'executed_actions' LIKE '%action_ghl_create_booking%'
           )::float / NULLIF(COUNT(DISTINCT CASE
                WHEN lower(direction) = 'inbound' THEN raw_payload_json->>'phone_number_from'
                ELSE raw_payload_json->>'phone_number_to'
           END), 0)
           FROM call_events WHERE created_at >= :a AND created_at < :b""",
        {"a": last_week_start, "b": last_week_end},
    ))

    # ── active_leads ──────────────────────────────────────────────────────────
    active_leads = _scalar(
        "SELECT COUNT(*) FROM lead_state"
        " WHERE (status IS NULL OR status NOT IN ('closed', 'terminal'))"
        " AND do_not_call IS NOT TRUE"
    ) or 0
    prev_active_leads = _scalar(
        "SELECT COUNT(*) FROM lead_state"
        " WHERE (status IS NULL OR status NOT IN ('closed', 'terminal'))"
        " AND do_not_call IS NOT TRUE"
        " AND created_at >= :s",
        {"s": w7d_start},
    ) or 0

    # ── in_vm_sequence ────────────────────────────────────────────────────────
    in_vm_sequence = _scalar("""
        SELECT COUNT(*) FROM lead_state
        WHERE ai_campaign_value IS NOT NULL
          AND ai_campaign_value != '3'
          AND (status IS NULL OR status NOT IN ('closed', 'terminal'))
          AND do_not_call IS NOT TRUE
    """) or 0
    prev_in_vm_sequence = _scalar("""
        SELECT COUNT(*) FROM lead_state
        WHERE ai_campaign_value IS NOT NULL
          AND ai_campaign_value != '3'
          AND (status IS NULL OR status NOT IN ('closed', 'terminal'))
          AND do_not_call IS NOT TRUE
          AND created_at >= :s
    """, {"s": w7d_start}) or 0

    # ── finalized_today (resets at midnight America/Chicago) ──────────────────
    finalized_today = int(_scalar(
        f"SELECT COUNT(*) FROM lead_state"
        f" WHERE (status IN ('closed', 'terminal') OR do_not_call IS TRUE OR ai_campaign_value = '3')"
        f"   AND updated_at >= {_midnight_cst}"
    ) or 0)
    finalized_yesterday = int(_scalar(
        f"SELECT COUNT(*) FROM lead_state"
        f" WHERE (status IN ('closed', 'terminal') OR do_not_call IS TRUE OR ai_campaign_value = '3')"
        f"   AND updated_at >= {_yesterday_start}"
        f"   AND updated_at < {_midnight_cst}"
    ) or 0)

    # ── sync_success_rate ─────────────────────────────────────────────────────
    sync_curr = _r(_scalar(
        "SELECT COUNT(*) FILTER (WHERE status = 'created')::float / NULLIF(COUNT(*), 0) FROM task_events WHERE created_at >= :s",
        {"s": w24_start},
    ))
    sync_prev = _r(_scalar(
        "SELECT COUNT(*) FILTER (WHERE status = 'created')::float / NULLIF(COUNT(*), 0) FROM task_events WHERE created_at BETWEEN :a AND :b",
        {"a": w48_start, "b": w24_start},
    ))

    # ── anomaly_count (exception types with ≥3 occurrences in 24h) ────────────
    anomaly_curr = _scalar("""
        SELECT COUNT(DISTINCT type) FROM (
            SELECT type, COUNT(*) AS cnt FROM exceptions
            WHERE created_at >= :s
            GROUP BY type HAVING COUNT(*) >= 3
        ) t
    """, {"s": w24_start}) or 0
    anomaly_prev = _scalar("""
        SELECT COUNT(DISTINCT type) FROM (
            SELECT type, COUNT(*) AS cnt FROM exceptions
            WHERE created_at BETWEEN :a AND :b
            GROUP BY type HAVING COUNT(*) >= 3
        ) t
    """, {"a": w48_start, "b": w24_start}) or 0

    # ── urgent_leads_count ────────────────────────────────────────────────────
    _URGENT_INTENTS = (
        "'enrolled','callback_request','callback_with_time',"
        "'re_engaged','human_transfer_request'"
    )
    urgent_curr = _scalar(f"""
        SELECT COUNT(*) FROM call_events
        WHERE created_at >= :w
          AND detected_intent IN ({_URGENT_INTENTS})
          AND COALESCE(duration_seconds, 0) >= 30
          AND transcript IS NOT NULL AND transcript != ''
          AND recording_url IS NOT NULL AND recording_url != ''
    """, {"w": w7d_start}) or 0
    urgent_prev = _scalar(f"""
        SELECT COUNT(*) FROM call_events
        WHERE created_at BETWEEN :a AND :b
          AND detected_intent IN ({_URGENT_INTENTS})
          AND COALESCE(duration_seconds, 0) >= 30
          AND transcript IS NOT NULL AND transcript != ''
          AND recording_url IS NOT NULL AND recording_url != ''
    """, {"a": w14d_start, "b": w7d_start}) or 0

    def _pt(val: Any, prev: Any) -> dict[str, Any]:
        return {"value": val, "previous_value": prev}

    return {
        "events_per_min":             _pt(round(ev_curr / 5, 1),  round(ev_prev / 5, 1)),
        "open_exceptions":            _pt(open_exc,                prev_open_exc),
        "backlog_size":               _pt(backlog,                 None),
        "active_alerts":              _pt(active_alerts,           None),
        "lookup_rate":                _pt(lookup_curr,             lookup_prev),
        "config_health":              _pt(config_health,           config_health),
        "calls_today":                _pt(calls_today,             calls_yesterday),
        "pickup_rate":                _pt(pickup_curr,             pickup_prev),
        "meaningful_engagement_rate": _pt(mer_curr,                mer_prev),
        "booking_rate":               _pt(book_curr,               book_prev),
        "active_leads":               _pt(active_leads,            prev_active_leads),
        "in_vm_sequence":             _pt(in_vm_sequence,          prev_in_vm_sequence),
        "finalized_today":            _pt(finalized_today,         finalized_yesterday),
        "sync_success_rate":          _pt(sync_curr,               sync_prev),
        "anomaly_count":              _pt(anomaly_curr,            anomaly_prev),
        "urgent_leads_count":         _pt(urgent_curr,             urgent_prev),
        "computed_at":                now.isoformat(),
    }


# ── Recent Calls / Sales Queue ────────────────────────────────────────────────

# Intent → base sales score (0-100).  Used to rank leads in the sales queue.
_INTENT_SCORES: dict[str, int] = {
    "enrolled":               95,
    "callback_request":       90,
    "callback_with_time":     88,
    "re_engaged":             85,
    "human_transfer_request": 80,
    "failed_booking":         70,
    "interested_not_now":     65,
    "partial_engagement":     55,
    "call_later_no_time":     50,
    "uncertain":              40,
    "request_sms":            35,
    "request_email":          30,
    "low_confidence_audio":   20,
    "not_interested":          5,
    "do_not_call":             0,
    "wrong_number":            0,
}


def _compute_sales_priority(
    intent: str | None, minutes_ago: float
) -> tuple[str, int, str]:
    """Return (priority, score, recommended_action) for a call row."""
    base = _INTENT_SCORES.get(intent or "", 30)
    recency = 10 if minutes_ago < 30 else (5 if minutes_ago < 120 else 0)
    score = min(100, base + recency)
    if score >= 80:
        return "urgent", score, "Call Now"
    if score >= 40:
        return "review", score, "Review"
    return "none", score, "Log & Move On"


def get_recent_calls(
    session: Session,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    voice_agent: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """
    Return calls with duration >= 30s that have both transcript and recording_url.
    Results are ordered by call start time descending (most recent first).
    Timestamps are returned as UTC ISO strings; the frontend converts to CST.

    Each row is enriched with sales queue fields:
      lead_name            — extracted from raw_payload_json (first/last/full_name) or "Unknown Lead"
      voice_agent          — raw voice_agent value from call_events
      attempts             — total call count for that contact in the query window
      transcript_preview   — first 120 chars of transcript
      sales_priority       — "urgent" | "review" | "none"
      sales_score          — 0-100
      last_call_minutes_ago — integer minutes since the call
      recommended_action   — "Call Now" | "Review" | "Log & Move On"

    voice_agent filter: "ColdLead" | "NewLead" | "Inbound" — matches ce.voice_agent.
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    from_dt = from_date or (now - timedelta(days=30))

    params: dict[str, Any] = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "limit": limit,
    }
    agent_filter = ""
    if voice_agent:
        agent_filter = "AND ce.voice_agent = :voice_agent"
        params["voice_agent"] = voice_agent

    rows = session.execute(text(f"""
        SELECT
            ce.contact_id,
            COALESCE(ls.normalized_phone, ce.contact_id) AS phone,
            COALESCE(ce.campaign_name, ls.campaign_name, ce.voice_agent) AS campaign_name,
            ce.status,
            COALESCE(ce.duration_seconds, 0)             AS duration_seconds,
            ce.recording_url,
            ce.transcript,
            ce.start_time_utc,
            ce.detected_intent,
            ce.created_at,
            ce.voice_agent,
            COALESCE(
                -- Primary: raw_payload_json->>'Name' unless it looks like a phone number
                CASE
                    WHEN TRIM(ce.raw_payload_json->>'Name') ~ '^\+?[\d\s\-\(\)\.]{{7,}}$'
                      OR TRIM(ce.raw_payload_json->>'Name') = ''
                      OR ce.raw_payload_json->>'Name' IS NULL
                    THEN NULL
                    ELSE TRIM(ce.raw_payload_json->>'Name')
                END,
                -- Fallback: GHL contact firstName from executed_actions (Inbound calls)
                NULLIF(TRIM(
                    ((ce.raw_payload_json->'executed_actions'
                        ->'get_the_user_preferences_from_gohighlevel'
                        ->>'return_value'
                    )::jsonb
                    ->'results'
                    ->'results.data'
                    ->'contact'
                    ->>'firstName')
                ), ''),
                'Unknown'
            ) AS lead_name,
            (
                SELECT COUNT(*)
                FROM call_events ce2
                WHERE ce2.contact_id = ce.contact_id
                  AND ce2.created_at BETWEEN :from_dt AND :to_dt
            ) AS attempts,
            LEFT(ce.transcript, 120) AS transcript_preview
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
          AND COALESCE(ce.duration_seconds, 0) >= 30
          AND ce.transcript IS NOT NULL AND ce.transcript != ''
          AND ce.recording_url IS NOT NULL AND ce.recording_url != ''
          {agent_filter}
        ORDER BY COALESCE(ce.start_time_utc, ce.created_at) DESC
        LIMIT :limit
    """), params).fetchall()

    calls = []
    for r in rows:
        start_ts = r[7] or r[9]
        if start_ts is not None:
            if hasattr(start_ts, "tzinfo") and start_ts.tzinfo is None:
                start_ts = start_ts.replace(tzinfo=timezone.utc)
            minutes_ago = max(0.0, (now - start_ts).total_seconds() / 60.0)
        else:
            minutes_ago = 999999.0
        priority, score, recommended = _compute_sales_priority(r[8], minutes_ago)
        calls.append({
            "contact_id": r[0],
            "phone": r[1],
            "campaign_name": r[2],
            "status": r[3],
            "duration_seconds": int(r[4]),
            "recording_url": r[5],
            "transcript": r[6],
            "call_time": start_ts.isoformat() if start_ts and hasattr(start_ts, "isoformat") else str(start_ts),
            "detected_intent": r[8],
            "voice_agent": r[10],
            "lead_name": r[11] or "Unknown",
            "attempts": int(r[12]) if r[12] else 1,
            "transcript_preview": r[13],
            "last_call_minutes_ago": int(minutes_ago) if minutes_ago < 999999 else None,
            "sales_priority": priority,
            "sales_score": score,
            "recommended_action": recommended,
        })

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "voice_agent_filter": voice_agent,
        "total": len(calls),
        "calls": calls,
    }


# ── Intent Calls Drill-Down ───────────────────────────────────────────────────

def get_intent_calls(
    session: Session,
    intent: str,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    campaign: str | None = None,
    voice_agent: str | None = None,
    direction: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Return individual calls matching a specific detected_intent.
    Used by the Engagement Analysis intent bar chart drill-down.
    Includes transcript and recording_url for call detail view.

    campaign:    "New Lead" | "Cold Lead" — filters on lead_state.campaign_name
    voice_agent: "ColdLead" | "NewLead" | "Inbound" — filters on ce.voice_agent
    direction:   "Outbound" | "Inbound" — filters on call_events.direction
    """
    now = datetime.now(tz=timezone.utc)
    to_dt = to_date or now
    from_dt = from_date or (now - timedelta(days=28))

    params: dict[str, Any] = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "intent": intent,
        "limit": limit,
    }
    campaign_filter = ""
    if campaign:
        campaign_filter = "AND ls.campaign_name = :campaign"
        params["campaign"] = campaign
    agent_filter = ""
    if voice_agent:
        agent_filter = "AND ce.voice_agent = :voice_agent"
        params["voice_agent"] = voice_agent
    direction_filter = ""
    if direction:
        if direction.lower() == "inbound":
            direction_filter = "AND LOWER(ce.direction) = 'inbound'"
        else:
            direction_filter = "AND (ce.direction IS NULL OR LOWER(ce.direction) != 'inbound')"

    rows = session.execute(text(f"""
        SELECT
            ce.contact_id,
            COALESCE(ls.normalized_phone, ce.contact_id)              AS phone,
            COALESCE(ce.campaign_name, ls.campaign_name, ce.voice_agent) AS display_agent,
            ce.status,
            COALESCE(ce.duration_seconds, 0)                          AS duration_seconds,
            ce.recording_url,
            ce.transcript,
            ce.start_time_utc,
            ce.detected_intent,
            ce.created_at
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.created_at BETWEEN :from_dt AND :to_dt
          AND ce.detected_intent = :intent
          {campaign_filter}
          {agent_filter}
          {direction_filter}
        ORDER BY COALESCE(ce.start_time_utc, ce.created_at) DESC
        LIMIT :limit
    """), params).fetchall()

    calls = []
    for r in rows:
        start_ts = r[7] or r[9]
        calls.append({
            "contact_id": r[0],
            "phone": r[1],
            "campaign_name": r[2],   # carries voice_agent value for display
            "status": r[3],
            "duration_seconds": int(r[4]),
            "recording_url": r[5],
            "transcript": r[6],
            "call_time": start_ts.isoformat() if start_ts and hasattr(start_ts, "isoformat") else str(start_ts),
            "detected_intent": r[8],
        })

    return {
        "period": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
        "intent": intent,
        "voice_agent_filter": voice_agent,
        "campaign_filter": campaign,
        "total": len(calls),
        "calls": calls,
    }


def get_webhook_failures(session: Session) -> dict[str, Any]:
    """
    Return webhook delivery failures for launch_outbound_call jobs in the last 24 hours.

    A failure is a completed launch_outbound_call with no matching call_events row
    within 4 hours after execution. Jobs executed within the last 20 minutes are
    excluded — Synthflow may still be delivering those webhooks.
    """
    summary_row = session.execute(text("""
        SELECT
            COUNT(*)                     AS total_launched,
            COUNT(ce.call_id)            AS got_webhook,
            COUNT(*) - COUNT(ce.call_id) AS missing
        FROM scheduled_jobs sj
        LEFT JOIN LATERAL (
            SELECT call_id FROM call_events
            WHERE contact_id = sj.payload_json->>'contact_id'
              AND created_at >= sj.updated_at - INTERVAL '10 minutes'
              AND created_at <= sj.updated_at + INTERVAL '4 hours'
            LIMIT 1
        ) ce ON true
        WHERE sj.job_type  = 'launch_outbound_call'
          AND sj.status    = 'completed'
          AND sj.updated_at >= NOW() - INTERVAL '24 hours'
          AND sj.updated_at <= NOW() - INTERVAL '20 minutes'
    """)).fetchone()

    total   = int(summary_row[0]) if summary_row else 0
    got     = int(summary_row[1]) if summary_row else 0
    missing = int(summary_row[2]) if summary_row else 0
    webhook_pct = round(100.0 * got / total) if total > 0 else None

    rows = session.execute(text("""
        SELECT
            sj.id                                                         AS job_id,
            sj.payload_json->>'contact_id'                                AS contact_id,
            sj.payload_json->>'campaign_name'                             AS campaign,
            sj.run_at                                                     AS placed_at,
            sj.updated_at                                                 AS executed_at,
            ROUND(EXTRACT(EPOCH FROM (NOW() - sj.updated_at)) / 60)::int AS minutes_since_execution
        FROM scheduled_jobs sj
        WHERE sj.job_type  = 'launch_outbound_call'
          AND sj.status    = 'completed'
          AND sj.updated_at >= NOW() - INTERVAL '24 hours'
          AND sj.updated_at <= NOW() - INTERVAL '20 minutes'
          AND NOT EXISTS (
              SELECT 1 FROM call_events ce
              WHERE ce.contact_id = sj.payload_json->>'contact_id'
                AND ce.created_at >= sj.updated_at - INTERVAL '10 minutes'
                AND ce.created_at <= sj.updated_at + INTERVAL '4 hours'
          )
        ORDER BY sj.updated_at DESC
        LIMIT 200
    """)).fetchall()

    failures = []
    for r in rows:
        failures.append({
            "job_id":                  r[0],
            "contact_id":              r[1],
            "campaign":                r[2],
            "placed_at":               r[3].isoformat() if r[3] and hasattr(r[3], "isoformat") else str(r[3]),
            "executed_at":             r[4].isoformat() if r[4] and hasattr(r[4], "isoformat") else str(r[4]),
            "minutes_since_execution": int(r[5]) if r[5] is not None else None,
        })

    return {
        "summary": {
            "total_launched": total,
            "got_webhook":    got,
            "missing":        missing,
            "webhook_pct":    webhook_pct,
        },
        "failures":     failures,
        "window_hours": 24,
        "recorded_at":  datetime.now(tz=timezone.utc).isoformat(),
    }
