"""
Explorer call queue sync job — nightly tagging of the accelerator ranking in GHL
(spec/49).

Follows the same claim/run/complete-or-fail/reschedule lifecycle as
staff_call_quality_scan_job. All filter-building and tagging logic lives in
app/services/explorer_queue.py::run_explorer_queue_cycle() — this module is
pure worker-lifecycle plumbing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SCHEDULE_INTERVAL_SECONDS = 86400  # daily
# Deliberately NOT tied to _SCHEDULE_INTERVAL_SECONDS (unlike
# staff_call_quality_scan, where lease == interval because that interval is
# already short). A full paginated sweep at this location's scale realistically
# finishes in minutes; a 24h lease would mean a genuinely stuck/crashed claim
# doesn't self-heal via recover_expired_claims() for a full day. Bounded
# separately so "how long this run may take" isn't coupled to "how often it
# repeats" — confirmed necessary 2026-09-18 when a claim got stuck (see
# incident note below) and would otherwise have sat unrecovered until
# tomorrow's scheduled run.
_CLAIM_LEASE_SECONDS = 3600
_JOB_TYPE = "explorer_queue_sync"


def explorer_queue_sync_job(job_id: str) -> None:
    """Entrypoint. Follows the same claim/run/complete/reschedule lifecycle as other scan jobs."""
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
    from app.worker.exceptions import create_exception

    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id=job_id, worker_id=worker_id, lease_seconds=_CLAIM_LEASE_SECONDS)
        if job is None:
            logger.info("explorer_queue_sync: could not claim job_id=%s — skipping", job_id)
            return

        mark_running(session, job)
        session.commit()

        try:
            from app.services.explorer_queue import run_explorer_queue_cycle
            # mode="apply": still a no-op unless explorer_queue_tagging_enabled is on.
            run_explorer_queue_cycle(session, settings, mode="apply")
            complete_job(session, job)
            session.commit()
        except Exception as exc:
            logger.error("explorer_queue_sync: cycle failed: %s", exc, exc_info=True)
            # A failed flush/write inside run_explorer_queue_cycle (e.g. the
            # explorer_queue_runs INSERT itself erroring) leaves this
            # session's transaction aborted — any further use (fail_job,
            # create_exception, _reschedule) raises PendingRollbackError
            # unless rolled back first. Confirmed live 2026-09-18: without
            # this rollback, _reschedule() also failed, and the job was left
            # permanently stuck in "running" with no next run ever
            # scheduled — exactly the orphaned-job bug class spec/29 guards
            # against, just via a different trigger (DB error, not a crash).
            session.rollback()
            fail_job(session, job, reason=str(exc))
            create_exception(
                session,
                type="explorer_queue_sync_failed",
                context={"job_id": job.id, "error": str(exc)},
                severity="warning",
                entity_type="system",
                entity_id=_JOB_TYPE,
            )
            session.commit()
        finally:
            _reschedule(session)
            session.commit()


def _reschedule(session: Session) -> None:
    from app.worker.scheduler import schedule_job

    run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_SCHEDULE_INTERVAL_SECONDS)
    schedule_job(
        session=session,
        job_type=_JOB_TYPE,
        entity_type="system",
        entity_id=_JOB_TYPE,
        run_at=run_at,
        payload={"_scheduled_by": "self"},
    )
    logger.debug("explorer_queue_sync: rescheduled in %ds", _SCHEDULE_INTERVAL_SECONDS)


def start_explorer_queue_sync() -> None:
    """Enqueue the first explorer_queue_sync job. Call once from worker startup."""
    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        existing = session.execute(text("""
            SELECT id FROM scheduled_jobs
            WHERE job_type = :job_type AND status = 'pending'
            LIMIT 1
        """), {"job_type": _JOB_TYPE}).fetchone()

        if existing:
            logger.info(
                "start_explorer_queue_sync: job already pending (id=%s) — skipping", existing[0],
            )
            return

        schedule_job(
            session=session,
            job_type=_JOB_TYPE,
            entity_type="system",
            entity_id=_JOB_TYPE,
            run_at=datetime.now(tz=timezone.utc),
            payload={"_scheduled_by": "startup"},
        )
        session.commit()
        logger.info("start_explorer_queue_sync: job enqueued")
