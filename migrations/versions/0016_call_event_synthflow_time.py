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
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE call_events ADD COLUMN IF NOT EXISTS synthflow_start_ms BIGINT"
    ))
    conn.execute(sa.text(
        "ALTER TABLE call_events ADD COLUMN IF NOT EXISTS call_timezone VARCHAR(100)"
    ))
    conn.execute(sa.text(
        "ALTER TABLE call_events ADD COLUMN IF NOT EXISTS call_time_local VARCHAR(50)"
    ))


def downgrade() -> None:
    op.drop_column("call_events", "call_time_local")
    op.drop_column("call_events", "call_timezone")
    op.drop_column("call_events", "synthflow_start_ms")
