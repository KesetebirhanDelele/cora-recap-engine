"""
AppConfig — runtime-editable business policy key/value store.

One row per setting key. Rows override the corresponding .env / Settings
defaults without requiring a process restart.

Reads go through app.core.app_config.get_config_value() which applies the
DB-first, settings-fallback chain. Direct ORM access is only for writes
(dashboard Settings page) and the migration seed.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AppConfig(Base):
    __tablename__ = "app_config"
    __table_args__ = (
        Index("idx_app_config_group", "group"),
    )

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    group: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))

    def __repr__(self) -> str:
        return f"<AppConfig key={self.key!r} value={self.value!r}>"
