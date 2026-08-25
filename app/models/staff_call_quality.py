"""
StaffCallQuality — quality analysis of a human staff member's call with a
lead or student, pulled from GHL's native-dialer Conversations data.

One row per GHL call message (ghl_message_id, unique). Distinct from
CallEvent, which is Cora's own AI-placed/answered calls — this table is
calls placed by human sales reps and support staff through GHL's own phone
system, which Cora has no other visibility into.

conversation_type is resolved by a priority chain (see
app/core/call_classification.py) and the raw signal snapshot columns
(who_you_are_value / select_option_1_value / select_option_2_value) are kept
verbatim specifically so it's possible to later query which of the three
near-duplicate GHL fields is actually populated in practice and decide which
one is authoritative — not yet known as of 2026-08-25 (spec/23).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Boolean

from app.models.base import Base


class StaffCallQuality(Base):
    __tablename__ = "staff_call_quality"
    __table_args__ = (
        UniqueConstraint("ghl_message_id", name="uq_staff_call_quality_message_id"),
        Index("idx_staff_call_quality_contact_id", "ghl_contact_id"),
        Index("idx_staff_call_quality_rep_user_id", "rep_user_id"),
        Index("idx_staff_call_quality_call_time", "call_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    ghl_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ghl_conversation_id: Mapped[Optional[str]] = mapped_column(String(255))
    ghl_contact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # rep_user_id: GHL user ID from the message's assignedTo/userId — whichever
    # rep placed or is assigned to this call. No pre-configured rep list; we
    # take whatever GHL reports (per Kes: "use assigned to for now").
    rep_user_id: Mapped[Optional[str]] = mapped_column(String(255))
    call_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    direction: Mapped[Optional[str]] = mapped_column(String(20))
    # call_connected: the outcome gate — False for busy/no-answer/voicemail-
    # only calls, which get no rubric score (nothing to judge conversation
    # quality on). True only for calls with an actual recording.
    call_connected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Routing: sales (lead) vs support (student) vs other ───────────────────
    # conversation_type: "sales" | "support" | "other" | "unknown"
    conversation_type: Mapped[Optional[str]] = mapped_column(String(20))
    # conversation_type_source: which signal resolved it — one of
    # "ghl_tags" | "ghl_who_you_are" | "ghl_select_option_1" |
    # "ghl_select_option_2" | "ghl_enrollment_date" | "call_history" |
    # "ai_inferred" | "unknown"
    conversation_type_source: Mapped[Optional[str]] = mapped_column(String(30))
    # Raw GHL contact tags at analysis time — the signal that actually
    # decided conversation_type when conversation_type_source == "ghl_tags";
    # kept for every row regardless of source for audit/debugging.
    ghl_tags: Mapped[Optional[list]] = mapped_column(JSON)
    # Raw snapshots of the three near-duplicate GHL picklist fields at analysis
    # time (values: "Potential Student" | "Current Student" | "Business or
    # Partner"), kept regardless of which one (if any) was actually used to
    # decide conversation_type — so real population data can be queried later.
    who_you_are_value: Mapped[Optional[str]] = mapped_column(String(50))
    select_option_1_value: Mapped[Optional[str]] = mapped_column(String(50))
    select_option_2_value: Mapped[Optional[str]] = mapped_column(String(50))
    ghl_enrollment_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # ── Transcript ──────────────────────────────────────────────────────────
    transcript_text: Mapped[Optional[str]] = mapped_column(Text)
    # transcript_source: "ghl_native" | "openai_whisper"
    transcript_source: Mapped[Optional[str]] = mapped_column(String(20))

    # ── Quality scoring (null until scored; stays null for non-connected calls) ─
    quality_score: Mapped[Optional[int]] = mapped_column(Integer)
    rubric_json: Mapped[Optional[dict]] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    flagged_reason: Mapped[Optional[str]] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<StaffCallQuality ghl_message_id={self.ghl_message_id!r} "
            f"conversation_type={self.conversation_type!r} score={self.quality_score}>"
        )
