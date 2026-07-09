"""Add ghl_oauth_tokens and ghl_conversation_log_events tables.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-08

Backs the GHL Marketplace OAuth app / Conversation Provider integration
(spec/19, spec/20) — a second, separate connection from the existing
Private Integration key (GHL_API_KEY, spec/16). Used only for writing
call recordings and transcripts into GHL Conversations activity.

  ghl_oauth_tokens          — one row per GHL location, access + refresh token
  ghl_conversation_log_events — dedupe record of call-log write attempts,
                                same shape/purpose as task_events (spec/16)

Partial unique index (Postgres-only):
  uq_ghl_conversation_log_events_one_success — at most one 'created' row
  per call event. Cannot be represented in SQLAlchemy model metadata
  directly and is created via op.execute() here (same pattern as
  uq_task_events_one_success in 0001_initial_schema.py).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision = "0018"


def upgrade() -> None:
    # ── ghl_oauth_tokens ─────────────────────────────────────────────────────
    op.create_table(
        "ghl_oauth_tokens",
        sa.Column("location_id", sa.String(255), primary_key=True),
        sa.Column("access_token", sa.String(2048), nullable=False),
        sa.Column("refresh_token", sa.String(2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── ghl_conversation_log_events ─────────────────────────────────────────
    op.create_table(
        "ghl_conversation_log_events",
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
        "idx_ghl_conversation_log_events_call_event",
        "ghl_conversation_log_events",
        ["call_event_id"],
    )
    op.create_index(
        "idx_ghl_conversation_log_events_status",
        "ghl_conversation_log_events",
        ["call_event_id", "status"],
    )
    # Partial unique index: at most one 'created' log event per call event
    op.execute(
        """
        CREATE UNIQUE INDEX uq_ghl_conversation_log_events_one_success
            ON ghl_conversation_log_events(call_event_id)
            WHERE status = 'created'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ghl_conversation_log_events_one_success")
    op.drop_index(
        "idx_ghl_conversation_log_events_status",
        table_name="ghl_conversation_log_events",
    )
    op.drop_index(
        "idx_ghl_conversation_log_events_call_event",
        table_name="ghl_conversation_log_events",
    )
    op.drop_table("ghl_conversation_log_events")
    op.drop_table("ghl_oauth_tokens")
