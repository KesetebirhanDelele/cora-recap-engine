"""Add wrong_date_incidents table + next_open_house_date config (spec/32).

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-30

wrong_date_incidents: one row per outbound SMS/email that told a lead the
wrong next-class-start or next-open-house date. outbound_message_id is UNIQUE
so the scanner is idempotent under retries and concurrent workers. The row's
status drives the dashboard "Wrong Date Monitor" tile (open -> corrected |
dismissed).

Also seeds app_config.next_open_house_date. ON CONFLICT DO NOTHING so a value
already saved from the dashboard is never overwritten.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wrong_date_incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("outbound_message_id", sa.String(36), nullable=False, unique=True),
        sa.Column("contact_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        # JSON list of {kind, raw, expected} — what was wrong in the message
        sa.Column("wrong_dates", sa.JSON, nullable=False),
        sa.Column("snippet", sa.Text, nullable=False),
        sa.Column("expected_class_start", sa.String(100), nullable=False, server_default=""),
        sa.Column("expected_open_house", sa.String(100), nullable=False, server_default=""),
        sa.Column("message_sent_at", sa.DateTime(timezone=True), nullable=False),
        # open | corrected | dismissed
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolved_by", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_text", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_wrong_date_incidents_status", "wrong_date_incidents", ["status", "created_at"])
    op.create_index("idx_wrong_date_incidents_contact", "wrong_date_incidents", ["contact_id"])

    op.execute(
        sa.text(
            """
            INSERT INTO app_config (key, value, description, "group", updated_by)
            VALUES ('next_open_house_date', 'October 29, 2026',
                    'Next Open House date shown in follow-up messages and checked by the wrong-date alert',
                    'brand', 'migration_0024')
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_config WHERE updated_by = 'migration_0024'")
    op.drop_index("idx_wrong_date_incidents_contact", table_name="wrong_date_incidents")
    op.drop_index("idx_wrong_date_incidents_status", table_name="wrong_date_incidents")
    op.drop_table("wrong_date_incidents")
