"""explorer_queue_contacts: exit_reason + callback_hold (spec/49).

exit_reason    why a contact left the queue (connected_call | not_interested | enrolled | wrong_number).
callback_hold  Rose has ent-out-callback on the contact (kept on the list on purpose).
Additive only; existing rows get exit_reason NULL / callback_hold FALSE.
drained_at keeps its name and now means "exited at".

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("explorer_queue_contacts", sa.Column("exit_reason", sa.String(30), nullable=True))
    op.add_column(
        "explorer_queue_contacts",
        sa.Column("callback_hold", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    )


def downgrade() -> None:
    op.drop_column("explorer_queue_contacts", "callback_hold")
    op.drop_column("explorer_queue_contacts", "exit_reason")
