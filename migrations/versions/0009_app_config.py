"""Add app_config table for runtime-editable business policy settings.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-01

New table:
  app_config — key/value store for tunable business policy.
  Rows override the corresponding .env defaults at runtime without
  requiring a process restart.
  Every write is journalled in audit_log (entity_type='app_config').

Seed rows are inserted for all tunable campaign policy fields so the
dashboard Settings page can display values immediately even before a
human has explicitly saved them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

# Seed values mirror the .env defaults so first boot is a no-op.
_SEED: list[dict] = [
    # ── New Lead active window ─────────────────────────────────────────────
    {
        "key": "new_lead_active_days",
        "value": "0,1,2,3,4,5,6",
        "description": "New Lead: active weekdays (0=Mon … 6=Sun, comma-separated)",
        "group": "new_lead_window",
    },
    {
        "key": "new_lead_active_start_hour",
        "value": "8",
        "description": "New Lead: calling window start hour (24-h, inclusive)",
        "group": "new_lead_window",
    },
    {
        "key": "new_lead_active_end_hour",
        "value": "22",
        "description": "New Lead: calling window end hour (24-h, exclusive)",
        "group": "new_lead_window",
    },
    # ── Cold Lead active window ────────────────────────────────────────────
    {
        "key": "cold_lead_active_days",
        "value": "0,1,2,3,4",
        "description": "Cold Lead: active weekdays (0=Mon … 6=Sun, comma-separated)",
        "group": "cold_lead_window",
    },
    {
        "key": "cold_lead_active_start_hour",
        "value": "8",
        "description": "Cold Lead: calling window start hour (24-h, inclusive)",
        "group": "cold_lead_window",
    },
    {
        "key": "cold_lead_active_end_hour",
        "value": "22",
        "description": "Cold Lead: calling window end hour (24-h, exclusive)",
        "group": "cold_lead_window",
    },
    # ── Cold Lead VM delays ────────────────────────────────────────────────
    {
        "key": "cold_vm_tier_none_delay_minutes",
        "value": "120",
        "description": "Cold Lead: delay (minutes) before first callback after voicemail (tier None→0)",
        "group": "cold_lead_vm",
    },
    {
        "key": "cold_vm_tier_0_delay_minutes",
        "value": "2880",
        "description": "Cold Lead: delay (minutes) before second callback (tier 0→1)",
        "group": "cold_lead_vm",
    },
    {
        "key": "cold_vm_tier_1_delay_minutes",
        "value": "2880",
        "description": "Cold Lead: delay (minutes) before third callback (tier 1→2)",
        "group": "cold_lead_vm",
    },
    {
        "key": "cold_vm_tier_2_finalizes",
        "value": "true",
        "description": "Cold Lead: finalize (stop) at tier 2 instead of scheduling tier 3",
        "group": "cold_lead_vm",
    },
    # ── New Lead VM delays ─────────────────────────────────────────────────
    {
        "key": "new_vm_tier_none_delay_minutes",
        "value": "120",
        "description": "New Lead: delay (minutes) before first callback after voicemail (tier None→0)",
        "group": "new_lead_vm",
    },
    {
        "key": "new_vm_tier_0_delay_minutes",
        "value": "1440",
        "description": "New Lead: delay (minutes) before second callback (tier 0→1)",
        "group": "new_lead_vm",
    },
    {
        "key": "new_vm_tier_1_delay_minutes",
        "value": "2880",
        "description": "New Lead: delay (minutes) before third callback (tier 1→2)",
        "group": "new_lead_vm",
    },
    {
        "key": "new_vm_tier_2_finalize",
        "value": "true",
        "description": "New Lead: finalize (stop) at tier 2 instead of scheduling tier 3",
        "group": "new_lead_vm",
    },
    # ── Messaging delays ───────────────────────────────────────────────────
    {
        "key": "sms_followup_delay_minutes",
        "value": "30",
        "description": "SMS follow-up: delay (minutes) after missed call / voicemail",
        "group": "messaging",
    },
    {
        "key": "email_followup_delay_days",
        "value": "1",
        "description": "Email follow-up: delay (days) after second missed call",
        "group": "messaging",
    },
]


def upgrade() -> None:
    op.create_table(
        "app_config",
        sa.Column("key", sa.String(100), primary_key=True, nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("group", sa.String(50), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("updated_by", sa.String(100), nullable=True),
    )
    op.create_index("idx_app_config_group", "app_config", ["group"])

    # Seed default rows
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
                "updated_by": "migration_0009",
            }
            for row in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_app_config_group", table_name="app_config")
    op.drop_table("app_config")
