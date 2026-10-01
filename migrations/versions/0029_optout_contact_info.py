"""optout_actions: the lead's full phone / email, so a review row can be looked up in GHL (spec/36).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("optout_actions", sa.Column("contact_phone", sa.String(40), nullable=True))
    op.add_column("optout_actions", sa.Column("contact_email", sa.String(160), nullable=True))


def downgrade() -> None:
    op.drop_column("optout_actions", "contact_email")
    op.drop_column("optout_actions", "contact_phone")
