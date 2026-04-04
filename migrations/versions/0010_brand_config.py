"""Add brand and messaging context variables to app_config.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-01

Adds app_config rows for brand/messaging context used by the
VM follow-up message generator:
  - brand_name, sender_name, reply_to_email, unsubscribe_text
  - next_class_start, live_open_house_link, explainer_open_house_video_link

Seed values from .env are used as defaults. All rows are editable at
runtime via the dashboard Settings > Brand & Messaging section.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_SEED: list[dict] = [
    {
        "key": "brand_name",
        "value": "Colaberry",
        "description": "Brand / school name used in messages",
        "group": "brand",
    },
    {
        "key": "sender_name",
        "value": "Cora from Colaberry",
        "description": "Sender display name used in message sign-off",
        "group": "brand",
    },
    {
        "key": "reply_to_email",
        "value": "admissions@colaberry.com",
        "description": "Reply-to email address shown in follow-up emails",
        "group": "brand",
    },
    {
        "key": "unsubscribe_text",
        "value": "Text STOP to stop alerts",
        "description": "Footer unsubscribe line appended to every SMS",
        "group": "brand",
    },
    {
        "key": "next_class_start",
        "value": "May 30, 2026",
        "description": "Next class start date shown in follow-up messages",
        "group": "brand",
    },
    {
        "key": "live_open_house_link",
        "value": "https://www.eventbrite.com/e/career-switch-open-house-session-may-21st-2026-tickets-1974943763962",
        "description": "Live Open House RSVP link included in follow-up messages",
        "group": "brand",
    },
    {
        "key": "explainer_open_house_video_link",
        "value": "https://www.youtube.com/watch?v=xLJcCCDCnis",
        "description": "7-min explainer / Open House video link included in messages",
        "group": "brand",
    },
]


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
            "app_config",
            sa.column("key", sa.String),
            sa.column("value", sa.Text),
            sa.column("description", sa.Text),
            sa.column("group", sa.String),
            sa.column("updated_by", sa.String),
        ),
        [
            {
                "key": row["key"],
                "value": row["value"],
                "description": row["description"],
                "group": row["group"],
                "updated_by": "migration_0010",
            }
            for row in _SEED
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app_config WHERE updated_by = 'migration_0010'"
    )
