"""
Nurture scheduler — periodic job that graduates leads from nurture → cold campaign.

Queries lead_state for contacts whose nurture window has expired:
  status = "nurture"
  next_action_at <= now
  do_not_call IS NOT TRUE
  invalid IS NOT TRUE

For each qualifying lead:
  1. transition_lead_state(lead, "timeout")  → status = "cold"
  2. enter_campaign(lead, "cold_lead")       → cancels retries, resets tier,
                                               schedules first outbound call

Scheduling cadence
------------------
  The job self-reschedules every NURTURE_SCHEDULER_INTERVAL_MINUTES (5 min).
  Duplicate scheduling is suppressed: if a pending run already exists the new
  one is skipped.

  On worker startup, ensure_scheduled() creates the first job if none is
  already pending.

Fault tolerance
---------------
  Individual lead failures are caught and logged — a bad row cannot stop the
  batch.  If the batch itself raises (e.g. DB connection lost), the job is
  marked failed but the scheduler still self-reschedules so the periodic
  cadence is never silently lost.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running

logger = logging.getLogger(__name__)

NURTURE_SCHEDULER_INTERVAL_MINUTES: int = 5
NURTURE_BATCH_SIZE: int = 50


# ---------------------------------------------------------------------------
# Main job function
# ---------------------------------------------------------------------------

def run_nurture_scheduler(job_id: str) -> None:
    """
    Periodic worker job: find due nurture leads and move them to cold campaign.

    Processes up to NURTURE_BATCH_SIZE leads per run to prevent long DB locks.
    Always self-reschedules — even on fatal failure — so the cadence is never
    silently dropped.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("run_nurture_scheduler: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        processed = 0
        errors = 0

        try:
            processed, errors = _process_due_nurture_leads(session, settings)
            logger.info(
                "run_nurture_scheduler: complete | processed=%d errors=%d job_id=%s",
                processed, errors, job_id,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "run_nurture_scheduler: fatal error | job_id=%s: %s", job_id, exc
            )
            fail_job(session, job, reason=str(exc))
            # Do not re-raise — we must still self-reschedule below.

        finally:
            # Always schedule the next run regardless of success/failure.
            # Duplicate guard inside _schedule_next_run prevents double-scheduling
            # on RQ retries or worker restarts.
            _schedule_next_run(session, settings)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def _process_due_nurture_leads(session, settings) -> tuple[int, int]:
    """
    Find and process all leads whose nurture window has expired.

    Returns (processed_count, error_count).
    """
    from sqlalchemy import or_, select

    from app.core.campaigns import enter_campaign
    from app.core.lifecycle import transition_lead_state
    from app.models.lead_state import LeadState

    now = datetime.now(tz=timezone.utc)

    leads = session.scalars(
        select(LeadState)
        .where(
            LeadState.status == "nurture",
            LeadState.next_action_at <= now,
            # Exclude suppressed contacts; treat NULL as not-suppressed
            or_(LeadState.do_not_call.is_(None), LeadState.do_not_call == False),  # noqa: E712
            or_(LeadState.invalid.is_(None), LeadState.invalid == False),           # noqa: E712
        )
        .limit(NURTURE_BATCH_SIZE)
    ).all()

    logger.info(
        "run_nurture_scheduler: found %d due nurture lead(s)", len(leads)
    )

    processed = 0
    errors = 0

    for lead in leads:
        try:
            new_status = transition_lead_state(session, lead, "timeout")
            if new_status:
                enter_campaign(session, lead, "cold_lead", settings=settings)
                processed += 1
                logger.info(
                    "run_nurture_scheduler: graduated | contact_id=%s → cold campaign",
                    lead.contact_id,
                )
            else:
                # Another worker already transitioned this lead (version conflict)
                logger.debug(
                    "run_nurture_scheduler: transition skipped (concurrent update) | "
                    "contact_id=%s",
                    lead.contact_id,
                )
        except Exception as exc:
            errors += 1
            logger.exception(
                "run_nurture_scheduler: failed for contact_id=%s: %s",
                lead.contact_id, exc,
            )

    return processed, errors


# ---------------------------------------------------------------------------
# Self-scheduling
# ---------------------------------------------------------------------------

def _schedule_next_run(session, settings) -> None:
    """
    Schedule the next nurture-scheduler run in NURTURE_SCHEDULER_INTERVAL_MINUTES.

    Skips if a pending run already exists so worker restarts and RQ retries
    cannot accumulate duplicate scheduler jobs.
    """
    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import schedule_job

    existing = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "run_nurture_scheduler",
            ScheduledJob.status == "pending",
        )
    ).first()
    if existing:
        return

    run_at = datetime.now(tz=timezone.utc) + timedelta(
        minutes=NURTURE_SCHEDULER_INTERVAL_MINUTES
    )
    schedule_job(
        session=session,
        job_type="run_nurture_scheduler",
        entity_type="system",
        entity_id="nurture_scheduler",
        run_at=run_at,
        payload={},
    )
    logger.debug(
        "run_nurture_scheduler: self-rescheduled | run_at=%s", run_at.isoformat()
    )


def ensure_scheduled(settings=None) -> None:
    """
    Ensure the nurture scheduler has at least one pending job.

    Called from worker/main.py on startup.  Idempotent — safe to call
    multiple times or concurrently (checked inside a transaction).
    """
    if settings is None:
        settings = get_settings()

    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        existing = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.job_type == "run_nurture_scheduler",
                ScheduledJob.status.in_(["pending", "claimed", "running"]),
            )
        ).first()

        if existing is None:
            schedule_job(
                session=session,
                job_type="run_nurture_scheduler",
                entity_type="system",
                entity_id="nurture_scheduler",
                run_at=datetime.now(tz=timezone.utc),
                payload={},
            )
            logger.info("ensure_scheduled: created initial run_nurture_scheduler job")
