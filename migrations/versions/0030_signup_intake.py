"""signup_daily / signup_snapshot: hourly snapshot of the Refactored sign-ups that land in GHL (spec/40).

signup_daily    one row per (Central day, segment): how many contacts were created that day and how many have an email,
                a phone, both, a campaign tag, a Facebook / Instagram tag. Rebuilt for the last 7 days every hour.
signup_snapshot one row per (hour, segment): cumulative totals at that hour, so the page can show WHEN emails / phones arrived
                (a lead who adds a phone next week moves into an earlier day's count in signup_daily, never in a snapshot).

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signup_daily",
        sa.Column("day", sa.Date, nullable=False),                      # America/Chicago calendar day of the GHL dateAdded
        sa.Column("segment", sa.String(12), nullable=False),            # explorer | open_house
        sa.Column("signups", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_email", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_phone", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_both", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_campaign", sa.Integer, nullable=False, server_default="0"),   # has a camp-* tag
        sa.Column("utm_fb", sa.Integer, nullable=False, server_default="0"),
        sa.Column("utm_ig", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("day", "segment"),
    )
    op.create_table(
        "signup_snapshot",
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),   # start of the hour, UTC
        sa.Column("segment", sa.String(12), nullable=False),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_email", sa.Integer, nullable=False, server_default="0"),
        sa.Column("with_phone", sa.Integer, nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("taken_at", "segment"),
    )


def downgrade() -> None:
    op.drop_table("signup_snapshot")
    op.drop_table("signup_daily")
