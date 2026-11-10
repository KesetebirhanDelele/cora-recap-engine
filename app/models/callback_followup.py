"""
CallbackFollowup - ledger of staff follow-ups Cora decided on (spec/50). One row per (source, source_id), so a retried job,
a restart or a duplicate event never double-books or double-emails.

source:  'call' (failed live transfer, source_id = call id) | 'email' (unanswered lead email, source_id = inbound message id)
action:  'book' (call-back reminder booked on the staff calendar) | 'email_staff' (email only)
status:  'dry_run' (flag off: decided and logged, nothing sent or booked) | 'done' | 'failed'
approval_status: shadow week (Kes 2026-11-10) - a 'dry_run' row waits as 'pending' on the dashboard; 'approved' makes Cora do the
         planned booking / staff email for that row, 'rejected' closes it (not a genuine request). NULL when the flag was on.
contacted_at: first connected staff call to the lead after the request (staff_call_quality), found by the hourly check.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CallbackFollowup(Base):
    __tablename__ = "callback_followups"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_callback_followups_source"),
        Index("idx_callback_followups_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    staff_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(15), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    appointment_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    requested_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # what the dashboard card shows and what an approval needs to act on later
    lead_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lead_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    lead_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    staff_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_start: Mapped[str | None] = mapped_column(String(40), nullable=True)      # ISO start of the planned booking
    approval_status: Mapped[str | None] = mapped_column(String(10), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
