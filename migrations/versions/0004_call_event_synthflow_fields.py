"""Add Synthflow-specific fields to call_events.

Revision ID: 0004
Revises: 0003
Create Date: 2026-03-11

New columns:
  model_id           — Synthflow assistant/model ID from completed-call payload
  lead_name          — Contact name as reported by Synthflow
  agent_phone_number — Synthflow agent's outbound phone number
  timeline           — Full Synthflow conversation timeline (JSON)
  telephony_duration — Telephony-reported call duration in seconds (float)
  telephony_start    — Telephony-reported call start time (UTC)
  telephony_end      — Telephony-reported call end time (UTC)

These fields support:
  - Correlation between Make Call and Call Completed workflows (model_id)
  - Richer reporting and debugging (timeline, telephony_*)
  - Operator-facing diagnostics (agent_phone_number, lead_name)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("call_events", sa.Column("model_id", sa.String(255), nullable=True))
    op.add_column("call_events", sa.Column("lead_name", sa.String(255), nullable=True))
    op.add_column("call_events", sa.Column("agent_phone_number", sa.String(50), nullable=True))
    op.add_column("call_events", sa.Column("timeline", sa.JSON(), nullable=True))
    op.add_column("call_events", sa.Column("telephony_duration", sa.Float(), nullable=True))
    op.add_column(
        "call_events",
        sa.Column("telephony_start", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "call_events",
        sa.Column("telephony_end", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_events", "telephony_end")
    op.drop_column("call_events", "telephony_start")
    op.drop_column("call_events", "telephony_duration")
    op.drop_column("call_events", "timeline")
    op.drop_column("call_events", "agent_phone_number")
    op.drop_column("call_events", "lead_name")
    op.drop_column("call_events", "model_id")
