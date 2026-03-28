"""
ShadowAction — audit record for every action intercepted by shadow mode.

One row is written per intercepted external action (outbound_call / sms / email).
Rows are only created when settings.shadow_mode_enabled is True.
This table is never written in production live mode.

action_type values:
  outbound_call  — Synthflow launch_new_lead_call suppressed
  sms            — send_sms_job suppressed
  email          — send_email_job suppressed
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class ShadowAction(Base):
    __tablename__ = "shadow_actions"
    __table_args__ = (
        Index("idx_shadow_actions_contact_id", "contact_id"),
        Index("idx_shadow_actions_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # outbound_call | sms | email
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<ShadowAction contact_id={self.contact_id!r} "
            f"action_type={self.action_type!r} created_at={self.created_at!r}>"
        )
