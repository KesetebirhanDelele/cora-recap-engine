"""
ClassificationResult — output of the AI call-analysis / lead-stage classifier.

One row per AI classification run. Multiple runs are allowed (prompt version
changes, re-analysis) but the latest is authoritative for routing decisions.

Stamped with model_used, prompt_family, prompt_version for rollback capability.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class ClassificationResult(Base):
    __tablename__ = "classification_results"
    __table_args__ = (
        Index("idx_classification_call_event", "call_event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("call_events.id", ondelete="RESTRICT"), nullable=False
    )
    model_used: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_family: Mapped[Optional[str]] = mapped_column(String(100))
    prompt_version: Mapped[Optional[str]] = mapped_column(String(50))
    output_json: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ClassificationResult call_event_id={self.call_event_id!r} "
            f"prompt={self.prompt_family}@{self.prompt_version}>"
        )
