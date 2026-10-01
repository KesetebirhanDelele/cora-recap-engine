"""optout_actions: opt-outs found in calls / SMS / email replies and what was done about them (spec/36).

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "optout_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contact_id", sa.String(100), nullable=False),           # real GHL contact id when known
        sa.Column("source", sa.String(20), nullable=False),               # call | sms_reply | email_reply | reconcile
        sa.Column("external_id", sa.String(100), nullable=True),          # GHL message id / call_event id
        sa.Column("kind", sa.String(16), nullable=False),                 # dnd | not_interested | wrong_number
        sa.Column("scope", sa.String(30), nullable=False, server_default=""),   # csv of call,sms,email
        sa.Column("confidence", sa.String(8), nullable=False, server_default="high"),
        sa.Column("decided_by", sa.String(20), nullable=False, server_default="auto"),   # auto | llm | kes
        sa.Column("status", sa.String(12), nullable=False),               # applied|review|ok|dismissed|failed|undone|shadow
        sa.Column("phrase", sa.String(200), nullable=True),
        sa.Column("excerpt", sa.String(300), nullable=True),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("previous_state", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_optout_status_created", "optout_actions", ["status", "created_at"])
    op.create_index("idx_optout_contact", "optout_actions", ["contact_id"])
    op.create_index(
        "uq_optout_source_external", "optout_actions", ["source", "external_id"],
        unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("optout_actions")
