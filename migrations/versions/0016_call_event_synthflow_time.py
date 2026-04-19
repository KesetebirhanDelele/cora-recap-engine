"""add synthflow start time, timezone, and local call time to call_events

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column("synthflow_start_ms", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "call_events",
        sa.Column("call_timezone", sa.String(100), nullable=True),
    )
    op.add_column(
        "call_events",
        sa.Column("call_time_local", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_events", "call_time_local")
    op.drop_column("call_events", "call_timezone")
    op.drop_column("call_events", "synthflow_start_ms")
