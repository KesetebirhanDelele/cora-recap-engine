"""
AlertEvent — active and historical alert state.

One row per alert instance. Written by the alerting service when a
metric threshold is breached or resolved.

status values:  active | resolved | acknowledged
severity values: critical | warning

Deduplication key: (alert_type, status='active').
Before inserting, check for an existing active row of the same type
within ALERT_DEDUP_WINDOW_SECONDS. If found, update last_seen_at only.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Double, String

from app.models.base import Base


class AlertEvent(Base):
    __tablename__ = "alert_events"
    __table_args__ = (
        Index("idx_alert_events_type_status", "alert_type", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    current_value: Mapped[float | None] = mapped_column(Double)
    threshold: Mapped[float | None] = mapped_column(Double)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AlertEvent alert_type={self.alert_type!r} "
            f"severity={self.severity!r} status={self.status!r}>"
        )
