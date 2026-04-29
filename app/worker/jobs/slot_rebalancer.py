"""
Periodic call-slot rebalancer.

Detects launch_outbound_call slots with more than _CALL_BATCH_SIZE (4) pending
jobs and redistributes them. Runs every _REBALANCE_INTERVAL_SECONDS (5 min)
as a self-rescheduling scheduled_job on the default queue.

Overages arise from concurrent scheduling races: two workers can both read the
pending count, both see N < 4, and both assign the same slot before either
commits. This job is the automatic repair for that window.

Registered in app/worker/main.py as "rebalance_call_slots".
Owned by the scheduler-host role (default/all) only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

# Must match _CALL_BATCH_SIZE / _CALL_SLOT_SECONDS in outbound_jobs.py
_CALL_BATCH_SIZE       = 4
_CALL_SLOT_SECONDS     = 300
_REBALANCE_INTERVAL_S  = 300  # how often to run: 5 minutes


_DETECT_SQL = """
SELECT COUNT(*) AS overage_slots
FROM (
    SELECT
        date_trunc('hour', run_at)
          + INTERVAL '5 min' * FLOOR(EXTRACT(minute FROM run_at) / 5) AS slot,
        COUNT(*) AS cnt
    FROM scheduled_jobs
    WHERE job_type = 'launch_outbound_call'
      AND status   = 'pending'
      AND run_at  >= NOW()
    GROUP BY 1
    HAVING COUNT(*) > :batch_size
) t
"""

# batch_size hardcoded in the FLOOR formula — must equal _CALL_BATCH_SIZE
_REDISTRIBUTE_SQL = """
WITH ranked AS (
    SELECT id, run_at,
           ROW_NUMBER() OVER (ORDER BY run_at ASC, id ASC) - 1 AS rn
    FROM scheduled_jobs
    WHERE job_type = 'launch_outbound_call'
      AND status   = 'pending'
      AND run_at  >= NOW()
),
base AS (SELECT GREATEST(NOW(), MIN(run_at)) AS t FROM ranked)
UPDATE scheduled_jobs sj
SET run_at = b.t + (FLOOR(r.rn / 4) * INTERVAL '5 minutes')
FROM ranked r, base b
WHERE sj.id = r.id
  AND sj.run_at != b.t + (FLOOR(r.rn / 4) * INTERVAL '5 minutes')
"""


def rebalance_call_slots_job(job_id: str) -> None:
    """
    1. Check for slots with > 4 pending calls.
    2. If any found: redistribute all pending calls into sequential 4/5-min slots.
    3. Reschedule self at now + 5 minutes.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.debug("rebalance_call_slots_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)

        try:
            from sqlalchemy import text

            overage_slots = session.scalar(
                text(_DETECT_SQL).bindparams(batch_size=_CALL_BATCH_SIZE)
            ) or 0

            if overage_slots > 0:
                result = session.execute(text(_REDISTRIBUTE_SQL))
                logger.info(
                    "rebalance_call_slots: redistributed %d rows across %d over-cap slot(s)",
                    result.rowcount, overage_slots,
                )
            else:
                logger.debug("rebalance_call_slots: all slots within cap")

            complete_job(session, job)

            schedule_job(
                session=session,
                job_type="rebalance_call_slots",
                entity_type="system",
                entity_id="slot_rebalancer",
                run_at=datetime.now(tz=timezone.utc) + timedelta(seconds=_REBALANCE_INTERVAL_S),
                payload={},
            )
            session.commit()

        except Exception as exc:
            logger.exception("rebalance_call_slots_job: error | job_id=%s: %s", job_id, exc)
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise


def start_slot_rebalancer() -> None:
    """
    Ensure exactly one pending rebalance_call_slots job exists.
    Called at worker startup from the scheduler-host role only.
    """
    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob

    with get_sync_session() as session:
        existing = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.job_type == "rebalance_call_slots",
                ScheduledJob.status.in_(["pending", "claimed", "running"]),
            )
        ).first()

        if existing:
            logger.debug("start_slot_rebalancer: job already exists | id=%s", existing.id)
            return

        schedule_job(
            session=session,
            job_type="rebalance_call_slots",
            entity_type="system",
            entity_id="slot_rebalancer",
            run_at=datetime.now(tz=timezone.utc),
            payload={},
        )
        session.commit()
        logger.info("start_slot_rebalancer: initial rebalance_call_slots job scheduled")
