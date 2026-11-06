"""
Explorer call queue tagging state (spec/49).

ExplorerQueueRun      one row per nightly cycle (aggregate counts) — run ledger,
                      shaped like TagAiColdLeadsRun plus untag/skip counters.
ExplorerQueueContact  one row per GHL contact the job has ever tagged. Holds the
                      time the queue tag was written (the drain rule compares
                      staff_call_quality.call_time against it), the last values
                      written (so Score / AI Summary are written only when
                      changed) and when the contact was drained.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExplorerQueueRun(Base):
    __tablename__ = "explorer_queue_runs"
    __table_args__ = (Index("idx_explorer_queue_runs_started_at", "started_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # running|completed|failed|skipped
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    view: Mapped[str | None] = mapped_column(String(20), nullable=True)  # reach_out|engaged
    rows_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_tagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_already_tagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_untagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_no_ghl_url: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_ai_campaign: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_no_access: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    contacts_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # True when the feed returned as many rows as we asked for (more may exist; no paging yet).
    feed_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ExplorerQueueRun id={self.id!r} status={self.status!r} tagged={self.contacts_tagged!r}>"


class ExplorerQueueContact(Base):
    __tablename__ = "explorer_queue_contacts"
    __table_args__ = (Index("idx_explorer_queue_contacts_drained_at", "drained_at"),)

    ghl_contact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ghl_location_id: Mapped[str] = mapped_column(String(64), nullable=False)
    enrollment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tagged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    drained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # = exited_at
    # Why the contact left the queue: connected_call | not_interested | enrolled | wrong_number.
    exit_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # True while Rose has ent-out-callback on the contact (kept on the list on purpose).
    callback_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_stage_tag: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_score_band_tag: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<ExplorerQueueContact {self.ghl_contact_id!r} drained={self.drained_at is not None}>"


class ExplorerQueueSkip(Base):
    """A feed row the job could not tag, kept so it can be reported back to Ali (one set per run).

    reason: no_ghl_contact (ghl_url empty/malformed) | wrong_subaccount (contact lives in a
    sub-account Cora cannot write to). The live feed has no name/email, so the report is keyed by
    enrollment_id / lead_id / rank.
    """
    __tablename__ = "explorer_queue_skips"
    __table_args__ = (Index("idx_explorer_queue_skips_run", "run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    enrollment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lead_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ghl_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # wrong_subaccount only
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
