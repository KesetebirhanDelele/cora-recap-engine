"""
Job scheduler — creates Postgres-authoritative job records and enqueues in RQ.

Canonical state is always Postgres. Redis/RQ is the execution rail only.
If Redis is cleared or a worker restarts, jobs can be recovered from
scheduled_jobs WHERE status = 'pending' and re-enqueued.

Separation of concerns:
  schedule_job()  — creates a ScheduledJob row and optionally enqueues in RQ
  enqueue_now()   — enqueues an already-pending DB job in RQ immediately
  cancel_job()    — delegates to claim.cancel_job() (re-exported here for callers)

RQ dependency is optional at scheduling time:
  - If Redis is unavailable, the job is stored in Postgres with status='pending'.
  - The worker recovery loop picks up pending jobs and enqueues them.
  - This ensures jobs survive worker and Redis restarts.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.scheduled_job import ScheduledJob
from app.worker.claim import cancel_job  # re-export for callers

logger = logging.getLogger(__name__)


def schedule_job(
    session: Session,
    job_type: str,
    entity_type: str,
    entity_id: str,
    run_at: datetime,
    payload: Optional[dict] = None,
    rq_queue: Optional[Any] = None,
    rq_job_func: Optional[Any] = None,
) -> ScheduledJob:
    """
    Create a durable scheduled job record in Postgres.

    If rq_queue and rq_job_func are provided AND run_at is now or in the past,
    the job is also enqueued in RQ immediately.

    For future jobs (run_at > now), the Postgres record is created and the
    worker recovery loop will enqueue the job when run_at arrives.

    This guarantees jobs survive Redis clears and worker restarts.
    """
    now = datetime.now(tz=timezone.utc)
    job_id = str(uuid.uuid4())
    job = ScheduledJob(
        id=job_id,
        job_type=job_type,
        entity_type=entity_type,
        entity_id=entity_id,
        run_at=run_at,
        status="pending",
        payload_json=payload or {},
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()

    logger.info(
        "schedule_job | id=%s type=%s entity=%s/%s run_at=%s",
        job_id, job_type, entity_type, entity_id, run_at.isoformat(),
    )

    # Enqueue immediately if RQ handles are provided and job is due
    is_due = run_at <= now
    if rq_queue is not None and rq_job_func is not None and is_due:
        try:
            rq_job = rq_queue.enqueue(rq_job_func, job_id)
            job.rq_job_id = rq_job.id
            session.flush()
            logger.info(
                "schedule_job: enqueued immediately | job_id=%s rq_job_id=%s",
                job_id, rq_job.id,
            )
        except Exception:
            logger.exception(
                "schedule_job: RQ enqueue failed — job stays pending in DB | job_id=%s",
                job_id,
            )
            # Job stays in Postgres as 'pending'; recovery loop will retry enqueue

    return job


def enqueue_now(
    session: Session,
    job: ScheduledJob,
    rq_queue: Any,
    rq_job_func: Any,
) -> bool:
    """
    Enqueue a pending Postgres job in RQ immediately.

    Used by the worker recovery loop to re-enqueue pending jobs after
    a Redis clear or worker restart.

    Returns True if enqueue succeeded, False otherwise (job stays pending).
    """
    if job.status != "pending":
        logger.warning(
            "enqueue_now: job not pending | job_id=%s status=%s",
            job.id, job.status,
        )
        return False

    try:
        rq_job = rq_queue.enqueue(rq_job_func, job.id)
        job.rq_job_id = rq_job.id
        session.flush()
        logger.info(
            "enqueue_now | job_id=%s rq_job_id=%s", job.id, rq_job.id
        )
        return True
    except Exception:
        logger.exception(
            "enqueue_now: RQ enqueue failed | job_id=%s", job.id
        )
        return False


__all__ = ["schedule_job", "enqueue_now", "cancel_job"]
