"""Add dashboard v2 tables: system_metrics, event_stream, alert_events.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-05

New tables (no FK constraints on existing tables):
  system_metrics  — pre-aggregated metric snapshots (60-second cadence)
  event_stream    — ordered system event log (real-time feed fallback)
  alert_events    — active and historical alert state

No existing tables are modified. This migration is fully reversible.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── system_metrics ────────────────────────────────────────────────────────
    op.create_table(
        "system_metrics",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("metric_name", sa.String(100), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_system_metrics_name_ts",
        "system_metrics",
        ["metric_name", "recorded_at"],
    )

    # ── event_stream ──────────────────────────────────────────────────────────
    op.create_table(
        "event_stream",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", sa.String(255), nullable=True),
        sa.Column("contact_id", sa.String(255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_event_stream_created_at",
        "event_stream",
        ["created_at"],
    )
    op.create_index(
        "idx_event_stream_contact_id",
        "event_stream",
        ["contact_id", "created_at"],
    )

    # ── alert_events ──────────────────────────────────────────────────────────
    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("alert_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("current_value", sa.Double(), nullable=True),
        sa.Column("threshold", sa.Double(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(255), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_alert_events_type_status",
        "alert_events",
        ["alert_type", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_alert_events_type_status", table_name="alert_events")
    op.drop_table("alert_events")

    op.drop_index("idx_event_stream_contact_id", table_name="event_stream")
    op.drop_index("idx_event_stream_created_at", table_name="event_stream")
    op.drop_table("event_stream")

    op.drop_index("idx_system_metrics_name_ts", table_name="system_metrics")
    op.drop_table("system_metrics")
