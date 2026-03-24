"""
OutboundMessage — record of every AI-generated SMS or email sent to a contact.

One row per send attempt. Status starts as 'pending' (content generated, delivery
may or may not have been attempted depending on provider integration).

channel: 'sms' | 'email'
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import String

from app.models.base import Base


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    __table_args__ = (
        Index("idx_outbound_messages_contact_id", "contact_id"),
        Index("idx_outbound_messages_channel", "contact_id", "channel"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # channel: 'sms' | 'email'
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    # subject: populated for email; None for SMS
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # status: 'pending' (generated), 'sent' (delivered), 'failed'
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<OutboundMessage contact_id={self.contact_id!r} "
            f"channel={self.channel!r} status={self.status!r}>"
        )
