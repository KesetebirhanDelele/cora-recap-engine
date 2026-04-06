"""
EventStream — ordered stream of system events for the activity feed.

One row per system event. Written by publish_event() in
app/services/event_publisher.py from worker job state transitions.

Acts as the durable fallback for the Redis Pub/Sub real-time channel.
Rows older than EVENT_STREAM_RETENTION_DAYS are purged weekly.

event_type values:
  job_started | job_completed | job_failed | exception_created |
  call_processed | campaign_switched | alert_triggered
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class EventStream(Base):
    __tablename__ = "event_stream"
    __table_args__ = (
        Index("idx_event_stream_created_at", "created_at"),
        Index("idx_event_stream_contact_id", "contact_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(255))
    contact_id: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<EventStream event_type={self.event_type!r} "
            f"entity_id={self.entity_id!r} created_at={self.created_at!r}>"
        )
