"""sms_send_ledger: one row per SMS Cora tries to send - the pre-send gate's budget + audit trail (spec/34).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sms_send_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contact_id", sa.String(100), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),          # followup | correction | test
        sa.Column("status", sa.String(12), nullable=False),          # reserved|sent|failed|blocked|deferred
        sa.Column("code", sa.String(60), nullable=False, server_default="ok"),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("segments", sa.Integer, nullable=False, server_default="0"),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("pacific_day", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_sms_ledger_created", "sms_send_ledger", ["created_at"])
    op.create_index("idx_sms_ledger_day_status", "sms_send_ledger", ["pacific_day", "status"])
    op.create_index("idx_sms_ledger_contact", "sms_send_ledger", ["contact_id", "created_at"])


def downgrade() -> None:
    op.drop_table("sms_send_ledger")
