"""
InboundMessage — record of every inbound SMS or email reply received from a contact.

Inserted by POST /v1/messages/inbound when a contact replies.
Writing this row also triggers lead_state.last_replied_at update and
cancellation of all pending jobs for the contact.

channel: 'sms' | 'email'
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import String

from app.models.base import Base


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (
        Index("idx_inbound_messages_contact_id", "contact_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # channel: 'sms' | 'email'
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<InboundMessage contact_id={self.contact_id!r} "
            f"channel={self.channel!r} received_at={self.received_at}>"
        )
