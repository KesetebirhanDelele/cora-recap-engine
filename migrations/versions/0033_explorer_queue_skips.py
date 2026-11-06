"""explorer_queue_skips + explorer_queue_runs.feed_capped (spec/49).

explorer_queue_skips  feed rows the job could not tag (no GHL contact / wrong sub-account), per run,
                      so they can be reported back to Ali.
explorer_queue_runs.feed_capped  feed returned as many rows as requested (more may exist).
Additive only.

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "explorer_queue_runs",
        sa.Column("feed_capped", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
    )
    op.create_table(
        "explorer_queue_skips",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("enrollment_id", sa.String(64), nullable=True),
        sa.Column("lead_id", sa.String(64), nullable=True),
        sa.Column("rank", sa.Integer, nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("primary_state", sa.String(40), nullable=True),
        sa.Column("ghl_location_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_explorer_queue_skips_run", "explorer_queue_skips", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_explorer_queue_skips_run", table_name="explorer_queue_skips")
    op.drop_table("explorer_queue_skips")
    op.drop_column("explorer_queue_runs", "feed_capped")
