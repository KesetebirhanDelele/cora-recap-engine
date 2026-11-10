"""callback_followups ledger (spec/50).

One row per staff follow-up Cora decided on after a failed live transfer or an unanswered lead email, unique per
(source, source_id) so retries never double-book or double-email. Additive only.

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "callback_followups",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("contact_id", sa.String(100), nullable=True),
        sa.Column("staff_key", sa.String(40), nullable=True),
        sa.Column("action", sa.String(15), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("appointment_id", sa.String(60), nullable=True),
        sa.Column("requested_time", sa.String(40), nullable=True),
        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("lead_name", sa.String(200), nullable=True),
        sa.Column("lead_phone", sa.String(40), nullable=True),
        sa.Column("lead_email", sa.String(200), nullable=True),
        sa.Column("staff_name", sa.String(120), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("plan_start", sa.String(40), nullable=True),
        sa.Column("approval_status", sa.String(10), nullable=True),
        sa.Column("decided_by", sa.String(100), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("source", "source_id", name="uq_callback_followups_source"),
    )
    op.create_index("idx_callback_followups_created_at", "callback_followups", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_callback_followups_created_at", table_name="callback_followups")
    op.drop_table("callback_followups")
