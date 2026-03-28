"""Add shadow_actions table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-03-28

New table:
  shadow_actions — audit log of actions intercepted by shadow mode.
  One row per intercepted outbound call / SMS / email.
  Never written when shadow_mode_enabled=false.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shadow_actions",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("contact_id", sa.String(255), nullable=False),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_shadow_actions_contact_id",
        "shadow_actions",
        ["contact_id"],
    )
    op.create_index(
        "idx_shadow_actions_created_at",
        "shadow_actions",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_shadow_actions_created_at", table_name="shadow_actions")
    op.drop_index("idx_shadow_actions_contact_id", table_name="shadow_actions")
    op.drop_table("shadow_actions")
