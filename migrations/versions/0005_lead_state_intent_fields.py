"""Add intent-driven fields to lead_state.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-22

New columns on lead_state:
  status           — active | nurture | closed  (nullable; existing rows = active)
  do_not_call      — boolean suppression flag   (default False)
  invalid          — boolean invalid-number flag (default False)
  preferred_channel — sms | email | None        (nullable)
  next_action_at   — UTC timestamp for nurture follow-up (nullable)

These fields are populated by app.core.intent_actions.handle_intent()
when a voicemail transcript yields a recognised intent (e.g. do_not_call,
interested_not_now, request_sms).  They allow the pipeline to stop retry
loops, suppress outreach, and route warm leads to a nurture campaign.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_state",
        sa.Column("status", sa.String(20), nullable=True),
    )
    op.add_column(
        "lead_state",
        sa.Column("do_not_call", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "lead_state",
        sa.Column("invalid", sa.Boolean(), nullable=True, server_default="false"),
    )
    op.add_column(
        "lead_state",
        sa.Column("preferred_channel", sa.String(20), nullable=True),
    )
    op.add_column(
        "lead_state",
        sa.Column("next_action_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Back-fill existing rows so they have an explicit status
    op.execute("UPDATE lead_state SET status = 'active' WHERE status IS NULL")


def downgrade() -> None:
    op.drop_column("lead_state", "next_action_at")
    op.drop_column("lead_state", "preferred_channel")
    op.drop_column("lead_state", "invalid")
    op.drop_column("lead_state", "do_not_call")
    op.drop_column("lead_state", "status")
