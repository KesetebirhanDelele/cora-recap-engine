"""Add report_excluded column to call_events.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-21

Adds a boolean flag that marks call_events rows that should be excluded
from voice performance and AI timeseries reporting.

Rationale: the JylDXjF8QB0Skr5cQzGGm webhook incident (week of 2026-04-13)
caused ~1,209 spurious call_events to be inserted alongside the 492 legitimate
calls. This column allows those rows to be silenced in reporting without
destroying the underlying data.

Usage:
  -- Mark illegitimate rows (see runbook or ops notes for the exact call_id list)
  UPDATE call_events SET report_excluded = TRUE WHERE ...;

  -- Revert an exclusion
  UPDATE call_events SET report_excluded = FALSE WHERE call_id = '...';

Fully reversible: down() drops the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision = "0017"


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column(
            "report_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
    )
    op.create_index(
        "idx_call_events_report_excluded",
        "call_events",
        ["report_excluded"],
        postgresql_where=sa.text("report_excluded = TRUE"),  # partial index — only indexes excluded rows
    )


def downgrade() -> None:
    op.drop_index("idx_call_events_report_excluded", table_name="call_events")
    op.drop_column("call_events", "report_excluded")
