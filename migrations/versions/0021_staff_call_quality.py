"""Add staff_call_quality table (spec/23).

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-25

Quality analysis of human sales-rep/support-staff calls pulled from GHL's
native-dialer Conversations data (recording + transcript), distinct from
CallEvent (Cora's own AI-placed/answered calls).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_call_quality",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ghl_message_id", sa.String(255), nullable=False),
        sa.Column("ghl_conversation_id", sa.String(255), nullable=True),
        sa.Column("ghl_contact_id", sa.String(255), nullable=False),
        sa.Column("rep_user_id", sa.String(255), nullable=True),
        sa.Column("call_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("direction", sa.String(20), nullable=True),
        sa.Column("call_connected", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
        sa.Column("conversation_type", sa.String(20), nullable=True),
        sa.Column("conversation_type_source", sa.String(30), nullable=True),
        sa.Column("ghl_tags", sa.JSON, nullable=True),
        sa.Column("who_you_are_value", sa.String(50), nullable=True),
        sa.Column("select_option_1_value", sa.String(50), nullable=True),
        sa.Column("select_option_2_value", sa.String(50), nullable=True),
        sa.Column("ghl_enrollment_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transcript_text", sa.Text, nullable=True),
        sa.Column("transcript_source", sa.String(20), nullable=True),
        sa.Column("quality_score", sa.Integer, nullable=True),
        sa.Column("rubric_json", sa.JSON, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("flagged_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_unique_constraint(
        "uq_staff_call_quality_message_id", "staff_call_quality", ["ghl_message_id"]
    )
    op.create_index(
        "idx_staff_call_quality_contact_id", "staff_call_quality", ["ghl_contact_id"]
    )
    op.create_index(
        "idx_staff_call_quality_rep_user_id", "staff_call_quality", ["rep_user_id"]
    )
    op.create_index(
        "idx_staff_call_quality_call_time", "staff_call_quality", ["call_time"]
    )


def downgrade() -> None:
    op.drop_index("idx_staff_call_quality_call_time", table_name="staff_call_quality")
    op.drop_index("idx_staff_call_quality_rep_user_id", table_name="staff_call_quality")
    op.drop_index("idx_staff_call_quality_contact_id", table_name="staff_call_quality")
    op.drop_constraint("uq_staff_call_quality_message_id", "staff_call_quality", type_="unique")
    op.drop_table("staff_call_quality")
