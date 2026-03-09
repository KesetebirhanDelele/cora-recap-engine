"""
TaskEvent — record of a GHL task creation attempt for a completed call.

At most one successful task per completed call event is permitted.
This is enforced by a partial unique index in the Postgres migration:
  CREATE UNIQUE INDEX uq_task_events_one_success
      ON task_events(call_event_id) WHERE status = 'created';

SQLite (used in unit tests) does not support partial unique indexes.
The application layer enforces this invariant before calling GHL.

status values: 'created' | 'failed'
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import String

from app.models.base import Base


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        Index("idx_task_events_call_event", "call_event_id"),
        Index("idx_task_events_status", "call_event_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("call_events.id", ondelete="RESTRICT"), nullable=False
    )
    provider_task_id: Mapped[Optional[str]] = mapped_column(String(255))
    # status: 'created' (GHL task exists) | 'failed' (attempt did not produce a task)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<TaskEvent call_event_id={self.call_event_id!r} "
            f"status={self.status!r} provider_task_id={self.provider_task_id!r}>"
        )
