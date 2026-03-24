"""Add outbound_messages, inbound_messages tables and lead_state.last_replied_at.

Revision ID: 0006
Revises: 0005
Create Date: 2026-03-22

New tables:
  outbound_messages — AI-generated SMS/email content sent to contacts
  inbound_messages  — replies received from contacts (reply-stop detection)

New column on lead_state:
  last_replied_at   — UTC timestamp set when an inbound reply is received;
                      suppresses all future messaging for the contact
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── outbound_messages ─────────────────────────────────────────────────────
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("contact_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_outbound_messages_contact_id",
        "outbound_messages",
        ["contact_id"],
    )
    op.create_index(
        "idx_outbound_messages_channel",
        "outbound_messages",
        ["contact_id", "channel"],
    )

    # ── inbound_messages ──────────────────────────────────────────────────────
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("contact_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_inbound_messages_contact_id",
        "inbound_messages",
        ["contact_id"],
    )

    # ── lead_state.last_replied_at ────────────────────────────────────────────
    op.add_column(
        "lead_state",
        sa.Column("last_replied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lead_state", "last_replied_at")
    op.drop_index("idx_inbound_messages_contact_id", table_name="inbound_messages")
    op.drop_table("inbound_messages")
    op.drop_index("idx_outbound_messages_channel", table_name="outbound_messages")
    op.drop_index("idx_outbound_messages_contact_id", table_name="outbound_messages")
    op.drop_table("outbound_messages")
