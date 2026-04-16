"""
Metrics collection job — computes system health metrics and evaluates alerts.

Self-rescheduling pattern (same as nurture_scheduler.py):
  - Runs every METRICS_COLLECTION_INTERVAL_SECONDS (default 60s).
  - Writes one system_metrics row per metric.
  - Calls evaluate_alerts() from app/services/alerting.py.
  - Cleans up expired event_stream and system_metrics rows per retention policy.
  - Reschedules itself at the end of each cycle.

Queue: default
Enqueue once at worker startup via start_metrics_scheduler().
Subsequent cycles self-reschedule; do NOT enqueue manually unless restarting.

Individual metric failures are caught and logged — they do not abort the cycle.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Metric names written to system_metrics table
_METRIC_NAMES = (
    "queue_lag_seconds",
    "active_workers",
    "open_exception_count",
    "stuck_job_count",
    "expired_lease_count",
    "jobs_completed_last_5m",
    "jobs_failed_last_5m",
    "error_rate",
)


def collect_metrics_job(job_id: str) -> None:
    """
    Metrics collection job entrypoint.

    1. Compute health metrics.
    2. Write to system_metrics table.
    3. Evaluate alert thresholds.
    4. Prune expired rows.
    5. Reschedule self.
    """
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
    from app.worker.exceptions import create_exception

    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id=job_id, worker_id=worker_id)
        if job is None:
            logger.info("collect_metrics_job: could not claim job_id=%s — skipping", job_id)
            return

        mark_running(session, job)
        session.commit()

        try:
            _run_cycle(session, settings)
            complete_job(session, job)
            session.commit()
        except Exception as exc:
            logger.error("collect_metrics_job: cycle failed: %s", exc, exc_info=True)
            fail_job(session, job, reason=str(exc))
            create_exception(
                session,
                type="metrics_collection_failed",
                context={"job_id": job.id, "error": str(exc)},
                severity="warning",
                entity_type="system",
                entity_id="metrics_collector",
            )
            session.commit()
        finally:
            # Always reschedule regardless of success/failure
            _reschedule(session, settings)
            session.commit()


def _run_cycle(session: Any, settings: Any) -> None:
    """Execute one full metrics collection cycle."""
    from app.models.system_metric import SystemMetric
    from app.services.alerting import evaluate_alerts
    from app.services.dashboard_metrics import get_health

    now = datetime.now(tz=timezone.utc)

    # 1. Compute health snapshot
    health = get_health(session)

    # 2. Write one system_metrics row per tracked metric
    for metric_name in _METRIC_NAMES:
        value = health.get(metric_name)
        if value is None:
            continue
        try:
            row = SystemMetric(
                id=str(uuid.uuid4()),
                metric_name=metric_name,
                value=float(value),
                labels={},
                recorded_at=now,
            )
            session.add(row)
        except Exception as exc:
            logger.warning("collect_metrics: failed to write metric %s: %s", metric_name, exc)

    session.flush()

    # 3. Evaluate alert thresholds
    try:
        evaluate_alerts(session, settings)
    except Exception as exc:
        logger.error("collect_metrics: alert evaluation failed: %s", exc)

    # 4. Prune expired rows
    try:
        _prune_expired_rows(session, settings, now)
    except Exception as exc:
        logger.error("collect_metrics: pruning failed: %s", exc)


def _prune_expired_rows(session: Any, settings: Any, now: datetime) -> None:
    """Delete expired event_stream and system_metrics rows per retention policy."""
    from sqlalchemy import text

    event_cutoff = now - timedelta(days=settings.event_stream_retention_days)
    metrics_cutoff = now - timedelta(days=settings.system_metrics_retention_days)

    deleted_events = session.execute(text("""
        DELETE FROM event_stream WHERE created_at < :cutoff
    """), {"cutoff": event_cutoff}).rowcount

    deleted_metrics = session.execute(text("""
        DELETE FROM system_metrics WHERE recorded_at < :cutoff
    """), {"cutoff": metrics_cutoff}).rowcount

    if deleted_events or deleted_metrics:
        logger.info(
            "collect_metrics: pruned event_stream=%d system_metrics=%d",
            deleted_events, deleted_metrics,
        )


def _reschedule(session: Any, settings: Any) -> None:
    """Schedule the next metrics collection run."""
    from datetime import timedelta

    from app.worker.scheduler import schedule_job

    interval = settings.metrics_collection_interval_seconds
    run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=interval)

    schedule_job(
        session=session,
        job_type="collect_metrics",
        entity_type="system",
        entity_id="metrics_collector",
        run_at=run_at,
        payload={"_scheduled_by": "self"},
    )
    logger.debug("collect_metrics: rescheduled in %ds", interval)


def start_metrics_scheduler() -> None:
    """
    Enqueue the first metrics collection job.
    Call once from worker startup if no pending metrics job exists.
    """
    from datetime import datetime, timezone

    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    settings = get_settings()

    with get_sync_session() as session:
        from sqlalchemy import text
        existing = session.execute(text("""
            SELECT id FROM scheduled_jobs
            WHERE job_type = 'collect_metrics' AND status = 'pending'
            LIMIT 1
        """)).fetchone()

        if existing:
            logger.info(
                "start_metrics_scheduler: metrics job already pending (id=%s) — skipping enqueue",
                existing[0],
            )
            return

        schedule_job(
            session=session,
            job_type="collect_metrics",
            entity_type="system",
            entity_id="metrics_collector",
            run_at=datetime.now(tz=timezone.utc),
            payload={"_scheduled_by": "startup"},
        )
        session.commit()
        logger.info("start_metrics_scheduler: metrics collection job enqueued")
