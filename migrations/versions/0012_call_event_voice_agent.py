"""Add voice_agent column to call_events, backfill from raw_payload_json Agent field.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-09

Voice agent is a per-call property distinct from the campaign (business concept).
Values: ColdLead | NewLead | Inbound  (extracted from Synthflow Agent field)

The Agent field in raw_payload_json contains strings like:
  "Cora Inbound - Completed Call"          → Inbound
  "Cora Outbound NewLead Completed Call"   → NewLead
  "Cora Outbound ColdLead Completed Call"  → ColdLead

Backfill logic mirrors the inference in app/api/routes/webhooks.py:
  ILIKE '%coldlead%' → ColdLead
  ILIKE '%newlead%'  → NewLead
  ILIKE '%inbound%'  → Inbound
  else               → NULL (unknown / pre-feature rows)

Fully reversible: down() drops the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision = "0011"


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column("voice_agent", sa.String(50), nullable=True),
    )

    # Backfill from raw_payload_json->>'Agent' using the same inference rules
    # as the webhook normalizer (case-insensitive substring match).
    op.execute("""
        UPDATE call_events
        SET voice_agent = CASE
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%coldlead%' THEN 'ColdLead'
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%newlead%'  THEN 'NewLead'
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%inbound%'  THEN 'Inbound'
            ELSE NULL
        END
        WHERE raw_payload_json IS NOT NULL
          AND raw_payload_json->>'Agent' IS NOT NULL
    """)

    op.create_index(
        "idx_call_events_voice_agent",
        "call_events",
        ["voice_agent"],
    )


def downgrade() -> None:
    op.drop_index("idx_call_events_voice_agent", table_name="call_events")
    op.drop_column("call_events", "voice_agent")
