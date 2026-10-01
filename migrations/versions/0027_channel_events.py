"""channel_events + channel_sync_state; inbound_messages.external_id (spec/35 delivery health).

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-01

channel_events: one row per
  handoff  - Cora wrote the GHL field that makes a workflow send an email / SMS
  delivery - GHL's own record of an outbound email / SMS and its provider status
  reply    - an inbound SMS / email / call from a lead (read from GHL conversations)
channel_sync_state: cursors for the GHL delivery sync (key -> json).
inbound_messages.external_id: the GHL message id, so a reply is stored once.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(12), nullable=False),            # handoff | delivery | reply
        sa.Column("channel", sa.String(10), nullable=False),         # email | sms | call
        sa.Column("contact_id", sa.String(100), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=True),     # GHL message id
        sa.Column("status_raw", sa.String(40), nullable=True),
        sa.Column("outcome", sa.String(12), nullable=True),          # delivered | failed | pending
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("source", sa.String(30), nullable=True),           # crm_job | correction | ghl_sync
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("detail", postgresql.JSONB, nullable=True),
    )
    op.create_index("idx_channel_events_kind_channel_at", "channel_events", ["kind", "channel", "event_at"])
    op.create_index("idx_channel_events_contact", "channel_events", ["contact_id", "channel", "event_at"])
    op.create_index(
        "uq_channel_events_external", "channel_events", ["kind", "channel", "external_id"],
        unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_table(
        "channel_sync_state",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", postgresql.JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("inbound_messages", sa.Column("external_id", sa.String(100), nullable=True))
    op.create_index(
        "uq_inbound_messages_external", "inbound_messages", ["external_id"],
        unique=True, postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_inbound_messages_external", table_name="inbound_messages")
    op.drop_column("inbound_messages", "external_id")
    op.drop_table("channel_sync_state")
    op.drop_table("channel_events")
