"""
AuditLog — append-only operator action trail.

Every dashboard operator action (retry, cancel, force-finalize, resolve, ignore)
writes one row. Rows are never updated or deleted.

Indexed on entity for efficient per-entity history queries.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, String

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # What was acted upon (exception | call | lead | scheduled_job)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # What happened
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # Who did it
    operator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Extra context (new_job_id, delay_minutes, reason, etc.)
    context_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog action={self.action!r} entity={self.entity_type}/{self.entity_id} "
            f"by={self.operator_id!r}>"
        )
