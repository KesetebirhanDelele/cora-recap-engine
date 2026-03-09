"""
Exception — surfaced failure record for operator dashboard visibility.

Created when:
  - call identity cannot be resolved (missing call_id or contact)
  - required campaign state cannot be read
  - GHL authentication fails
  - retry budget is exhausted

Operator actions mutate status via optimistic concurrency (version):
  - Retry Now: re-enqueues a job; does not change exception status until job completes
  - Mark Resolved / Ignored: sets status with reason code
  - Cancel Future Jobs: marks scheduled jobs cancelled; exception stays open until confirmed
  - Force Finalize: executes terminal writes; resolves exception on success

All operator mutations must check version before updating (compare-and-swap).
Concurrent operator actions: first wins, second gets a conflict error.

status values: open | resolved | ignored
severity values: critical | warning

version: incremented on each status transition.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class ExceptionRecord(Base):
    __tablename__ = "exceptions"
    __table_args__ = (
        Index("idx_exceptions_status", "status", "created_at"),
        Index("idx_exceptions_call_event", "call_event_id"),
        Index("idx_exceptions_entity", "entity_type", "entity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # call_event_id is nullable: some exceptions exist before a call_event row is created
    call_event_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("call_events.id", ondelete="RESTRICT")
    )
    entity_type: Mapped[Optional[str]] = mapped_column(String(50))
    entity_id: Mapped[Optional[str]] = mapped_column(String(255))
    # type: identity_resolution | ghl_auth | tier_invalid | retry_budget_exhausted | ...
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    # severity: critical | warning
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="critical")
    # status: open | resolved | ignored
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    resolution_reason: Mapped[Optional[str]] = mapped_column(String(500))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255))
    context_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # version: optimistic concurrency guard for operator actions
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ExceptionRecord type={self.type!r} severity={self.severity!r} "
            f"status={self.status!r}>"
        )
