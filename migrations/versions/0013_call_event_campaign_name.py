"""Add campaign_name column to call_events, backfill from raw_payload_json.

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-10

campaign_name records the business campaign at the time of the call — the
value normalised from the Synthflow Agent field by the webhook intake layer.

Values: "New Lead" | "Cold Lead" | "Inbound"

Distinct from voice_agent (which stores the Synthflow agent identifier:
"NewLead" / "ColdLead" / "Inbound"). campaign_name uses the human-readable
business label so the dashboard can filter and display it without translation.

Backfill logic mirrors webhooks.py normalize_synthflow_payload():
  Agent ILIKE '%coldlead%' → "Cold Lead"
  Agent ILIKE '%newlead%'  → "New Lead"
  Agent ILIKE '%inbound%'  → "Inbound"
  campaign_name in payload → use that value as fallback
  else                     → NULL (pre-feature rows)

Fully reversible: down() drops the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision = "0012"


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column("campaign_name", sa.String(100), nullable=True),
    )

    # Backfill: infer from Agent field first, fall back to campaign_name key in payload.
    op.execute("""
        UPDATE call_events
        SET campaign_name = CASE
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%coldlead%' THEN 'Cold Lead'
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%newlead%'  THEN 'New Lead'
            WHEN LOWER(raw_payload_json->>'Agent') LIKE '%inbound%'  THEN 'Inbound'
            WHEN raw_payload_json->>'campaign_name' IS NOT NULL
                 AND raw_payload_json->>'campaign_name' != ''
                 THEN raw_payload_json->>'campaign_name'
            ELSE NULL
        END
        WHERE raw_payload_json IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column("call_events", "campaign_name")
