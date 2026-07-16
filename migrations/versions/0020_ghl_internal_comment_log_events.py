"""Add ghl_internal_comment_log_events table.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-16

Backs a third, separate GHL write mechanism (its own Private Integration
token, settings.ghl_conversations_api_key) that posts a staff-only
InternalComment containing the call transcript + recording link into a
contact's Conversations tab — no OAuth Marketplace app or Conversation
Provider required, confirmed working against both sandbox and production
GHL accounts on 2026-07-16.

  ghl_internal_comment_log_events — dedupe record of write attempts, same
                                     shape/purpose as ghl_conversation_log_events

Partial unique index (Postgres-only):
  uq_ghl_internal_comment_log_events_one_success — at most one 'created' row
  per call event. Same pattern as uq_ghl_conversation_log_events_one_success
  in 0019_ghl_oauth_conversation_tables.py.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision = "0019"


def upgrade() -> None:
    op.create_table(
        "ghl_internal_comment_log_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "call_event_id",
            sa.String(36),
            sa.ForeignKey("call_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ghl_message_id", sa.String(255)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_ghl_internal_comment_log_events_call_event",
        "ghl_internal_comment_log_events",
        ["call_event_id"],
    )
    op.create_index(
        "idx_ghl_internal_comment_log_events_status",
        "ghl_internal_comment_log_events",
        ["call_event_id", "status"],
    )
    # Partial unique index: at most one 'created' log event per call event
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ghl_internal_comment_log_events_one_success
            ON ghl_internal_comment_log_events(call_event_id)
            WHERE status = 'created'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ghl_internal_comment_log_events_one_success")
    op.drop_index(
        "idx_ghl_internal_comment_log_events_status",
        table_name="ghl_internal_comment_log_events",
    )
    op.drop_index(
        "idx_ghl_internal_comment_log_events_call_event",
        table_name="ghl_internal_comment_log_events",
    )
    op.drop_table("ghl_internal_comment_log_events")
