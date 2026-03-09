"""
ScheduledJob — durable canonical record of every queued or delayed job.

This is the source of truth for job state. Redis/RQ holds execution handles
only. If Redis is cleared or a worker restarts, jobs can be re-queued from
Postgres scheduled_jobs where status = 'pending'.

Claim/lease semantics (from autonomous execution contract §5.4):
  - A worker atomically sets status='claimed', claimed_by, claimed_at,
    lease_expires_at, and increments version in a single transaction.
  - No other worker may claim the same job (enforced by version check).
  - Abandoned claims (lease_expires_at < NOW()) are re-claimable.
  - Terminal failures set status='failed' and open an exception record.

status values:
  pending   — waiting to be claimed
  claimed   — claimed by a worker, not yet running
  running   — worker is actively executing
  completed — successfully finished
  failed    — terminal failure; exception record should exist
  cancelled — cancelled by operator action

version: incremented on every status transition; used for optimistic
         concurrency so only one worker can claim a given job.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        Index("idx_scheduled_jobs_entity", "entity_type", "entity_id", "status"),
        Index("idx_scheduled_jobs_run_at", "run_at", "status"),
        Index("idx_scheduled_jobs_status", "status", "run_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rq_job_id: Mapped[Optional[str]] = mapped_column(String(255))
    # status: pending | claimed | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    claimed_by: Mapped[Optional[str]] = mapped_column(String(255))
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # version: incremented on each state transition; guards atomic claim
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ScheduledJob job_type={self.job_type!r} entity_id={self.entity_id!r} "
            f"status={self.status!r} run_at={self.run_at}>"
        )
