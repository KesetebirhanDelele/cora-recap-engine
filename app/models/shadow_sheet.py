"""
ShadowSheetRow — mirrored row from Google Sheets during shadow mode.

Production routing must never read from this table. It exists only to:
  1. Keep a live copy of Sheets data during cutover.
  2. Support reconciliation reporting (drift detection between Sheets and DB).
  3. Enable side-by-side comparison as Postgres is validated.

reconciliation_status values:
  pending  — row mirrored, not yet compared
  matched  — row matches Postgres authoritative data
  drift    — row differs from authoritative data (surface in dashboard)
  error    — reconciliation check itself failed

Unique constraint: (sheet_name, source_row_id) — one mirrored row per sheet row.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class ShadowSheetRow(Base):
    __tablename__ = "shadow_sheet_rows"
    __table_args__ = (
        UniqueConstraint(
            "sheet_name", "source_row_id", name="uq_shadow_sheet_rows_identity"
        ),
        Index("idx_shadow_sheet_name_status", "sheet_name", "reconciliation_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_row_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON)
    mirrored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # reconciliation_status: pending | matched | drift | error
    reconciliation_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
    )

    def __repr__(self) -> str:
        return (
            f"<ShadowSheetRow sheet={self.sheet_name!r} "
            f"row={self.source_row_id!r} status={self.reconciliation_status!r}>"
        )
