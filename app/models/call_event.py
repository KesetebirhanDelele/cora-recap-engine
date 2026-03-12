"""
CallEvent — idempotent record of a single call webhook event.

One row per dedupe_key. The dedupe_key encodes (call_id, action_type) and
prevents duplicate irreversible actions when the same event is replayed.

Unique constraint: dedupe_key.
Index: call_id (lookup by external call ID), created_at (time-range queries).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String, Float

from app.models.base import Base


class CallEvent(Base):
    __tablename__ = "call_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_call_events_dedupe_key"),
        Index("idx_call_events_call_id", "call_id"),
        Index("idx_call_events_created_at", "created_at"),
        Index("idx_call_events_contact_id", "contact_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # contact_id populated during GHL enrichment; nullable until enrichment succeeds
    contact_id: Mapped[Optional[str]] = mapped_column(String(255))
    direction: Mapped[Optional[str]] = mapped_column(String(20))
    # status: completed | voicemail | hangup_on_voicemail | queue | in-progress | failed
    status: Mapped[Optional[str]] = mapped_column(String(50))
    end_call_reason: Mapped[Optional[str]] = mapped_column(String(100))
    transcript: Mapped[Optional[str]] = mapped_column(Text)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    recording_url: Mapped[Optional[str]] = mapped_column(Text)
    start_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Synthflow-specific fields from completed-call payload
    model_id: Mapped[Optional[str]] = mapped_column(String(255))  # Synthflow assistant/model ID
    lead_name: Mapped[Optional[str]] = mapped_column(String(255))
    agent_phone_number: Mapped[Optional[str]] = mapped_column(String(50))
    # timeline: full Synthflow conversation timeline JSON
    timeline: Mapped[Optional[list]] = mapped_column(JSON)
    # telephony timing (Synthflow-reported, may differ from duration_seconds)
    telephony_duration: Mapped[Optional[float]] = mapped_column(Float)
    telephony_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    telephony_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # dedupe_key format: "{call_id}:{action_type}" — unique per (call, action) pair
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # raw_payload_json: original webhook body preserved for replay
    raw_payload_json: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<CallEvent call_id={self.call_id!r} status={self.status!r}>"
