"""staff_call_quality.answered_by_human (spec/49 addendum, 2026-11-19).

call_connected is TRUE for a voicemail the rep left a 20+ second message on. This nullable column holds the
transcript judge's verdict that a live person actually answered. Additive only; NULL = not judged yet.

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_call_quality", sa.Column("answered_by_human", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_call_quality", "answered_by_human")
