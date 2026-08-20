"""
GhlOAuthToken — per-location OAuth token pair for the GHL Marketplace app.

Separate from GHL_API_KEY (the static Private Integration token used by
GHLClient — see spec/16). This table backs the OAuth Marketplace app /
Conversation Provider integration (spec/19, spec/20), used only for writing
call recordings and transcripts into GHL Conversations activity.

One row per GHL location. Access tokens expire ~24h; refresh tokens ~1yr
and rotate on use — both fields are overwritten in place on refresh, no
history is kept.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GhlOAuthToken(Base):
    __tablename__ = "ghl_oauth_tokens"

    location_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    access_token: Mapped[str] = mapped_column(String(2048), nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(2048), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<GhlOAuthToken location_id={self.location_id!r} expires_at={self.expires_at!r}>"
