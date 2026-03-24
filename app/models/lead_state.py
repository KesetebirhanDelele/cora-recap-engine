"""
LeadState — authoritative campaign state per GHL contact.

One row per contact_id. Updated in-place via optimistic concurrency (version).
Campaign value tracks voicemail tier progression: None → 0 → 1 → 2 → 3.

Unique constraint: contact_id (one state row per contact).
Index: normalized_phone (lookup by phone during event enrichment).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LeadState(Base):
    __tablename__ = "lead_state"
    __table_args__ = (
        UniqueConstraint("contact_id", name="uq_lead_state_contact_id"),
        Index("idx_lead_state_phone", "normalized_phone"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_phone: Mapped[Optional[str]] = mapped_column(String(20))
    lead_stage: Mapped[Optional[str]] = mapped_column(String(100))
    campaign_name: Mapped[Optional[str]] = mapped_column(String(100))
    ai_campaign: Mapped[Optional[str]] = mapped_column(String(10))
    # ai_campaign_value: None | '0' | '1' | '2' | '3'
    ai_campaign_value: Mapped[Optional[str]] = mapped_column(String(10))
    last_call_status: Mapped[Optional[str]] = mapped_column(String(50))
    # ── Intent-driven fields (populated by handle_intent) ─────────────────────
    # status: active | nurture | closed
    status: Mapped[Optional[str]] = mapped_column(String(20))
    do_not_call: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    invalid: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    # preferred_channel: sms | email | None (default = voice)
    preferred_channel: Mapped[Optional[str]] = mapped_column(String(20))
    # next_action_at: scheduled time for nurture follow-up
    next_action_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # last_replied_at: set when an inbound SMS/email reply is received; suppresses future messaging
    last_replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # version: incremented on every update; used for optimistic concurrency checks
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<LeadState contact_id={self.contact_id!r} "
            f"ai_campaign_value={self.ai_campaign_value!r} version={self.version}>"
        )
