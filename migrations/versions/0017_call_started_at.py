"""add call_started_at to call_events — canonical UTC call time

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-19

Why:
  created_at = webhook receipt time (unreliable for backlogged/batch imports).
  call_started_at = actual call start time in UTC, populated from
  synthflow_start_ms (epoch ms) or raw_payload_json->>'start_time'.
  Dashboard queries must use COALESCE(call_started_at, created_at).
"""
from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column("call_started_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Backfill from synthflow_start_ms (already imported for ~22k rows)
    op.execute("""
        UPDATE call_events
        SET call_started_at = to_timestamp(synthflow_start_ms / 1000.0)
        WHERE synthflow_start_ms IS NOT NULL
          AND call_started_at IS NULL
    """)


def downgrade() -> None:
    op.drop_column("call_events", "call_started_at")
