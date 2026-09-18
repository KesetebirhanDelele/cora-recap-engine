"""
TagAiColdLeadsRun — run-history log for the AI cold lead tagging batch job.

One row per daily cycle (aggregate counts, not per-contact — spec/31).
Written by app/services/ai_cold_lead_tagging.py::run_tagging_cycle().
Read by the dashboard tile (GET /dashboard/tag-ai-cold-leads/runs and the
ai_cold_lead_tagging_tagged_24h card-metric).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TagAiColdLeadsRun(Base):
    __tablename__ = "tag_ai_cold_leads_runs"
    __table_args__ = (
        Index("idx_tag_ai_cold_leads_runs_started_at", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running|completed|failed|skipped
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    contacts_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_tagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_skipped_already_tagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<TagAiColdLeadsRun id={self.id!r} status={self.status!r} "
            f"contacts_tagged={self.contacts_tagged!r}>"
        )
