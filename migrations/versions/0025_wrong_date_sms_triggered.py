"""wrong_date_incidents: per-channel delivery ledger + reset never-delivered corrections (spec/32).

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-30

The first version of the correction wrote only GHL's "Message:" field. Neither GHL
workflow reads that field (email sends Support Issue Ticket #2, SMS sends Support
issue Ticket #4), so NO correction was ever delivered even though 219 incidents were
marked 'corrected'. This migration:

  * adds email_triggered_at / sms_triggered_at (stamped when a correction is actually
    written to the field its workflow reads) and correction_channel;
  * reopens the incidents that were marked corrected without ever being triggered
    (sms_triggered_at IS NULL), so the normal "send to all" flow delivers them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wrong_date_incidents", sa.Column("sms_triggered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("wrong_date_incidents", sa.Column("email_triggered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("wrong_date_incidents", sa.Column("correction_channel", sa.String(10), nullable=True))
    op.execute(
        """
        UPDATE wrong_date_incidents
        SET status = 'open', resolved_by = NULL, resolved_at = NULL, correction_text = NULL
        WHERE status = 'corrected'
        """
    )


def downgrade() -> None:
    op.drop_column("wrong_date_incidents", "correction_channel")
    op.drop_column("wrong_date_incidents", "email_triggered_at")
    op.drop_column("wrong_date_incidents", "sms_triggered_at")
