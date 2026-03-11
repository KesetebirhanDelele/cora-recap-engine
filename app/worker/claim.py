"""
Scheduled job claim/lease service.

Implements the atomic claim/release semantics from the autonomous execution
contract (spec/12 §5.4):

  - A worker claims a job using an atomic state change (version check).
  - Claimed work records claimed_by, claimed_at, and lease_expires_at.
  - Abandoned or expired claims may be re-queued safely.
  - Only one worker may hold a claim at a time.

All state changes use optimistic concurrency via the `version` column.
A version mismatch means another worker claimed first — treat as a no-op.

No business logic lives here — this module is pure scheduling mechanics.
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models.scheduled_job import ScheduledJob

logger = logging.getLogger(__name__)

DEFAULT_LEASE_SECONDS = 300  # 5 minutes


def get_worker_id() -> str:
    """Return a unique identifier for the current worker process."""
    return f"{socket.gethostname()}-{os.getpid()}"


def claim_job(
    session: Session,
    job_id: str,
    worker_id: str,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> ScheduledJob | None:
    """
    Atomically claim a pending scheduled job.

    Uses optimistic concurrency: the UPDATE WHERE version = expected_version
    ensures only one worker succeeds when two race to claim the same job.

    Returns the refreshed job object if the claim succeeded, None otherwise.
    Callers must NOT proceed with job execution if this returns None.
    """
    job = session.get(ScheduledJob, job_id)
    if job is None:
        logger.warning("claim_job: job not found | job_id=%s", job_id)
        return None

    if job.status != "pending":
        logger.info(
            "claim_job: job not claimable | job_id=%s status=%s",
            job_id, job.status,
        )
        return None

    expected_version = job.version
    now = datetime.now(tz=timezone.utc)

    result: CursorResult = session.execute(  # type: ignore[assignment]
        update(ScheduledJob)
        .where(
            ScheduledJob.id == job_id,
            ScheduledJob.status == "pending",
            ScheduledJob.version == expected_version,
        )
        .values(
            status="claimed",
            claimed_by=worker_id,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            version=expected_version + 1,
            updated_at=now,
        )
    )
    session.flush()

    if result.rowcount == 0:
        logger.info(
            "claim_job: version conflict — another worker claimed first | job_id=%s",
            job_id,
        )
        return None

    session.refresh(job)
    logger.info(
        "claim_job: claimed | job_id=%s worker=%s lease_seconds=%d",
        job_id, worker_id, lease_seconds,
    )
    return job


def mark_running(session: Session, job: ScheduledJob) -> None:
    """Advance a claimed job to 'running' status."""
    now = datetime.now(tz=timezone.utc)
    session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.id == job.id, ScheduledJob.version == job.version)
        .values(status="running", version=job.version + 1, updated_at=now)
    )
    session.flush()
    session.refresh(job)


def complete_job(session: Session, job: ScheduledJob) -> None:
    """Mark a running job as completed."""
    now = datetime.now(tz=timezone.utc)
    session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.id == job.id)
        .values(
            status="completed",
            version=job.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    logger.info("complete_job | job_id=%s job_type=%s", job.id, job.job_type)


def fail_job(session: Session, job: ScheduledJob, reason: str = "") -> None:
    """
    Mark a job as permanently failed.

    Called when retry budget is exhausted or a terminal exception occurs.
    The caller should also create an ExceptionRecord (see app/worker/exceptions.py).
    """
    now = datetime.now(tz=timezone.utc)
    session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.id == job.id)
        .values(
            status="failed",
            version=job.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    logger.error(
        "fail_job | job_id=%s job_type=%s reason=%r",
        job.id, job.job_type, reason,
    )


def cancel_job(session: Session, job_id: str) -> bool:
    """
    Cancel a pending or claimed job by ID.

    Returns True if the job was cancelled, False if it was already in a
    terminal state (completed, failed, cancelled) or not found.
    Operator cancel-future-jobs action uses this per scheduled job.
    """
    job = session.get(ScheduledJob, job_id)
    if job is None:
        return False
    if job.status in ("completed", "failed", "cancelled"):
        return False

    now = datetime.now(tz=timezone.utc)
    result: CursorResult = session.execute(  # type: ignore[assignment]
        update(ScheduledJob)
        .where(
            ScheduledJob.id == job_id,
            ScheduledJob.status.in_(["pending", "claimed"]),
        )
        .values(status="cancelled", version=job.version + 1, updated_at=now)
    )
    session.flush()
    cancelled = result.rowcount > 0
    if cancelled:
        logger.info("cancel_job | job_id=%s job_type=%s", job_id, job.job_type)
    return cancelled


def recover_expired_claims(
    session: Session,
    worker_id: str,
    batch_size: int = 50,
) -> list[str]:
    """
    Find jobs with expired leases and reset them to 'pending'.

    Called periodically by the worker recovery loop. This ensures that
    jobs claimed by a crashed or stuck worker are eventually re-processed.

    Returns the list of job IDs that were recovered.
    """
    now = datetime.now(tz=timezone.utc)
    # Fetch expired claimed jobs
    from sqlalchemy import select
    expired = session.scalars(
        select(ScheduledJob)
        .where(
            ScheduledJob.status == "claimed",
            ScheduledJob.lease_expires_at < now,
        )
        .limit(batch_size)
    ).all()

    recovered_ids: list[str] = []
    for job in expired:
        result: CursorResult = session.execute(  # type: ignore[assignment]
            update(ScheduledJob)
            .where(ScheduledJob.id == job.id, ScheduledJob.version == job.version)
            .values(
                status="pending",
                claimed_by=None,
                claimed_at=None,
                lease_expires_at=None,
                version=job.version + 1,
                updated_at=now,
            )
        )
        if result.rowcount > 0:
            recovered_ids.append(job.id)
            logger.warning(
                "recover_expired_claims: reset expired claim | job_id=%s "
                "original_worker=%s",
                job.id, job.claimed_by,
            )
    if recovered_ids:
        session.flush()
    return recovered_ids
