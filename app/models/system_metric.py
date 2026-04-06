"""
SystemMetric — pre-aggregated time-series metric snapshots.

One row per metric per collection cycle (60-second default).
Written by collect_metrics_job in app/worker/jobs/metrics_jobs.py.
Read by GET /dashboard/health and GET /dashboard/metrics.

Rows older than SYSTEM_METRICS_RETENTION_DAYS are purged on the weekly
cleanup cycle inside collect_metrics_job.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, Double, String

from app.models.base import Base


class SystemMetric(Base):
    __tablename__ = "system_metrics"
    __table_args__ = (
        Index("idx_system_metrics_name_ts", "metric_name", "recorded_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Double, nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<SystemMetric metric_name={self.metric_name!r} "
            f"value={self.value!r} recorded_at={self.recorded_at!r}>"
        )
