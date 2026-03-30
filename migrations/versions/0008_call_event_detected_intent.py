"""Add detected_intent column to call_events.

Revision ID: 0008
Revises: 0007
Create Date: 2026-03-29

Stores the rule-based intent detected from the call transcript at processing
time.  Written by ai_jobs.py (completed calls) and voicemail_jobs.py
(voicemail calls with transcripts).  NULL means no intent was detected or
the call predates this feature.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_events",
        sa.Column("detected_intent", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("call_events", "detected_intent")
