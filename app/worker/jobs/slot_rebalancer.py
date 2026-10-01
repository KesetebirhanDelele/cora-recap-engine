"""
Periodic call-slot rebalancer.

Detects launch_outbound_call slots with more than _CALL_BATCH_SIZE (4) pending
jobs and spreads ONLY THE EXCESS later. Runs every _REBALANCE_INTERVAL_SECONDS
(5 min) as a self-rescheduling scheduled_job on the default queue.

Overages arise from concurrent scheduling races: two workers can both read the
pending count, both see N < 4, and both assign the same slot before either
commits. This job is the automatic repair for that window.

INVARIANT (fixed 2026-10-01): a job's run_at is NEVER moved earlier, and a job
in a slot that is within capacity is never touched. The previous version
re-ranked EVERY pending future call into consecutive slots starting from "now"
whenever any single slot was over capacity, which pulled retries configured for
24/48 hours out to ~4.5 hours (queue length / 48 per hour) and put 3-4 calls (and
3-4 emails) on one lead in a single day. Lead-requested callbacks (exact-time) are
immovable: they hold their slot and others route around them.

Registered in app/worker/main.py as "rebalance_call_slots".
Owned by the scheduler-host role (default/all) only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.core.call_policy import CALLBACK_INTENT_REASONS
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

# Must match _CALL_BATCH_SIZE / _CALL_SLOT_SECONDS in outbound_jobs.py
_CALL_BATCH_SIZE       = 4
_CALL_SLOT_SECONDS     = 300
_REBALANCE_INTERVAL_S  = 300  # how often to run: 5 minutes


# Reasons whose run_at is an exact time the LEAD asked for - never moved.
IMMOVABLE_INTENT_REASONS = CALLBACK_INTENT_REASONS
_MAX_SLOT_SEARCH = 7 * 24 * 12  # 7 days of 5-minute slots


def _slot_start(dt: datetime) -> datetime:
    """Start of the 5-minute slot containing dt (epoch-aligned, same grid as outbound_jobs)."""
    ts = int(dt.timestamp())
    return datetime.fromtimestamp(ts - ts % _CALL_SLOT_SECONDS, tz=timezone.utc)


def plan_redistribution(
    rows: list[tuple[str, datetime, bool]],
) -> list[tuple[str, datetime]]:
    """
    Pure planner. rows = [(job_id, run_at, immovable)] for pending FUTURE calls.
    Returns [(job_id, new_run_at)] for the jobs that must move.

    - Every slot keeps up to _CALL_BATCH_SIZE jobs where they are.
    - Immovable jobs claim their slot first (they may even exceed capacity; nothing
      is moved on their account except movable neighbours).
    - Excess movable jobs go to the earliest LATER slot with room (first fit),
      at offset k * 75 s where k is that slot's current occupancy.
    - new_run_at is always > the job's original run_at; non-excess jobs never move.
    """
    from collections import defaultdict
    from datetime import timedelta

    def aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    occupancy: dict[datetime, int] = defaultdict(int)
    movable: list[tuple[str, datetime]] = []
    for job_id, run_at, immovable in sorted(rows, key=lambda r: (aware(r[1]), r[0])):
        run_at = aware(run_at)
        if immovable:
            occupancy[_slot_start(run_at)] += 1
        else:
            movable.append((job_id, run_at))

    moves: list[tuple[str, datetime]] = []
    spacing = _CALL_SLOT_SECONDS // _CALL_BATCH_SIZE
    for job_id, run_at in movable:
        slot = _slot_start(run_at)
        if occupancy[slot] < _CALL_BATCH_SIZE:
            occupancy[slot] += 1
            continue
        target = slot
        for _ in range(_MAX_SLOT_SEARCH):
            target = target + timedelta(seconds=_CALL_SLOT_SECONDS)
            if occupancy[target] < _CALL_BATCH_SIZE:
                break
        new_run_at = target + timedelta(seconds=occupancy[target] * spacing)
        occupancy[target] += 1
        moves.append((job_id, new_run_at))
    return moves


def redistribute_overages(session) -> int:
    """Apply plan_redistribution to the pending future launch_outbound_call jobs. Returns jobs moved."""
    from sqlalchemy import select, text, update

    from app.models.scheduled_job import ScheduledJob
    from app.worker.jobs.outbound_jobs import _BUCKET_ALLOC_LOCK_KEY

    # Same advisory lock the schedulers hold while allocating buckets (Postgres only).
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BUCKET_ALLOC_LOCK_KEY})

    now = datetime.now(tz=timezone.utc)
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
            ScheduledJob.run_at >= now,
        )
    ).all()
    by_id = {j.id: j for j in jobs}
    rows = [
        (j.id, j.run_at, (j.payload_json or {}).get("intent_reason") in IMMOVABLE_INTENT_REASONS)
        for j in jobs
    ]
    moved = 0
    for job_id, new_run_at in plan_redistribution(rows):
        job = by_id[job_id]
        if new_run_at <= (job.run_at if job.run_at.tzinfo else job.run_at.replace(tzinfo=timezone.utc)):
            continue  # defence in depth: never move a call earlier
        result = session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.id == job_id, ScheduledJob.status == "pending",
                   ScheduledJob.version == job.version)
            .values(run_at=new_run_at, version=job.version + 1, updated_at=now)
        )
        moved += result.rowcount or 0
    return moved


def rebalance_call_slots_job(job_id: str) -> None:
    """
    1. Find slots with > 4 pending calls.
    2. Move only the excess to the earliest later slot with room; never earlier.
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
            moved = redistribute_overages(session)
            if moved:
                logger.info(
                    "rebalance_call_slots: moved %d excess call(s) to later slots (none moved earlier)",
                    moved,
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
