"""
Sign-up intake sync job - hourly summary of the Refactored sign-ups in GHL (spec/40).

Same claim / run / complete-or-fail / reschedule lifecycle as ai_cold_lead_tagging_scan_job. The work lives in
app/services/signup_intake.py::run_sync(); this module is worker-lifecycle plumbing only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_SCHEDULE_INTERVAL_SECONDS = 3600
_CLAIM_LEASE_SECONDS = 900          # a run reads a few pages of GHL contacts; a stuck claim self-heals in 15 min
_JOB_TYPE = "signup_intake_sync"


def signup_intake_sync_job(job_id: str) -> None:
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
    from app.worker.exceptions import create_exception

    settings = get_settings()
    with get_sync_session() as session:
        job = claim_job(session, job_id=job_id, worker_id=get_worker_id(), lease_seconds=_CLAIM_LEASE_SECONDS)
        if job is None:
            logger.info("signup_intake_sync: could not claim job_id=%s - skipping", job_id)
            return
        mark_running(session, job)
        session.commit()
        try:
            from app.services.signup_intake import run_sync
            run_sync(session, settings)
            complete_job(session, job)
            session.commit()
        except Exception as exc:
            logger.error("signup_intake_sync: failed: %s", exc, exc_info=True)
            session.rollback()          # an aborted transaction would also break fail_job / create_exception / _reschedule
            fail_job(session, job, reason=str(exc))
            create_exception(
                session, type="signup_intake_sync_failed", context={"job_id": job.id, "error": str(exc)},
                severity="warning", entity_type="system", entity_id=_JOB_TYPE,
            )
            session.commit()
        finally:
            _reschedule(session)
            session.commit()


def _reschedule(session: Session) -> None:
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session, job_type=_JOB_TYPE, entity_type="system", entity_id=_JOB_TYPE,
        run_at=datetime.now(tz=timezone.utc) + timedelta(seconds=_SCHEDULE_INTERVAL_SECONDS),
        payload={"_scheduled_by": "self"},
    )


def start_signup_intake_sync() -> None:
    """Enqueue the first signup_intake_sync job. Call once from worker startup; does nothing if one is already pending."""
    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        existing = session.execute(text(
            "SELECT id FROM scheduled_jobs WHERE job_type = :t AND status = 'pending' LIMIT 1"), {"t": _JOB_TYPE}).fetchone()
        if existing:
            logger.info("start_signup_intake_sync: job already pending (id=%s) - skipping", existing[0])
            return
        schedule_job(
            session=session, job_type=_JOB_TYPE, entity_type="system", entity_id=_JOB_TYPE,
            run_at=datetime.now(tz=timezone.utc), payload={"_scheduled_by": "startup"},
        )
        session.commit()
        logger.info("start_signup_intake_sync: job enqueued")
