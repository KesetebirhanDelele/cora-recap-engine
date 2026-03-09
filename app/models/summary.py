"""
SummaryResult — student summary and consent detection output.

Unique per call_event_id: exactly one summary record per completed call.
The consent gate reads summary_consent to decide whether to write to GHL.

summary_consent values: 'YES' | 'NO' | 'UNKNOWN'
  - 'YES'     → writeback to GHL is allowed
  - 'NO'      → writeback must NOT occur (spec must-not)
  - 'UNKNOWN' → treat as NO; do not write

Unique constraint: call_event_id (one summary per call event).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import String

from app.models.base import Base


class SummaryResult(Base):
    __tablename__ = "summary_results"
    __table_args__ = (
        UniqueConstraint("call_event_id", name="uq_summary_results_call_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("call_events.id", ondelete="RESTRICT"), nullable=False
    )
    student_summary: Mapped[Optional[str]] = mapped_column(Text)
    summary_offered: Mapped[Optional[bool]] = mapped_column(Boolean)
    # summary_consent: YES | NO | UNKNOWN
    summary_consent: Mapped[Optional[str]] = mapped_column(String(20))
    model_used: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_family: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<SummaryResult call_event_id={self.call_event_id!r} "
            f"consent={self.summary_consent!r}>"
        )
