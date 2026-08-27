"""Add lead_name/lead_phone to staff_call_quality (spec/23 follow-up).

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-27

Dashboard needs the lead's name/phone alongside each call row, not just
ghl_contact_id. Populated at write time from the already-fetched GHL
contact record — no extra API calls. Existing rows (backfilled before this
migration) will have NULL here.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staff_call_quality", sa.Column("lead_name", sa.String(255), nullable=True))
    op.add_column("staff_call_quality", sa.Column("lead_phone", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("staff_call_quality", "lead_phone")
    op.drop_column("staff_call_quality", "lead_name")
