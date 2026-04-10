"""Add sales outcome fields to lead_state for sales queue logging.

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-10

Five new columns on lead_state:
  sales_outcome      VARCHAR(100) — booked | follow_up | not_interested | no_answer |
                                    voicemail | wrong_number
  sales_next_action  VARCHAR(100) — rep-entered label (e.g. "Call Back", "Send SMS")
  sales_follow_up_at TIMESTAMPTZ  — when to follow up (required when next_action set)
  sales_notes        VARCHAR(200) — optional rep notes
  sales_updated_by   VARCHAR(100) — agent_id of the rep who submitted the outcome
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_state", sa.Column("sales_outcome",     sa.String(100), nullable=True))
    op.add_column("lead_state", sa.Column("sales_next_action", sa.String(100), nullable=True))
    op.add_column("lead_state", sa.Column("sales_follow_up_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("lead_state", sa.Column("sales_notes",       sa.String(200), nullable=True))
    op.add_column("lead_state", sa.Column("sales_updated_by",  sa.String(100), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_state", "sales_updated_by")
    op.drop_column("lead_state", "sales_notes")
    op.drop_column("lead_state", "sales_follow_up_at")
    op.drop_column("lead_state", "sales_next_action")
    op.drop_column("lead_state", "sales_outcome")
