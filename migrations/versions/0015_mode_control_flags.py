"""Seed mode-control flags into app_config for dashboard-driven live/shadow switching.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-16

No schema changes — app_config table already exists (migration 0009).
This migration adds seed rows for the 9 operational mode flags so the
System Controls dashboard page has values to read and toggle immediately.

Flags seeded (group='mode_control'):
  shadow_mode_enabled          — outbound calls / SMS / email (true = intercepted)
  ghl_write_mode               — "shadow" | "live"
  ghl_write_shadow_log_only    — suppress shadow payload persistence when true
  ghl_write_contact_fields     — allow contact field writes in live mode
  ghl_write_tasks              — allow task creation in live mode
  ghl_write_summary            — allow student summary writeback in live mode
  ghl_write_campaign_state     — allow AI Campaign field updates in live mode
  ghl_write_finalization       — allow AI Campaign = No finalization writes
  system_paused                — when true, workers hold jobs without executing

Seed values mirror the current .env defaults so the first read is a no-op.
Workers that already check these via get_mode_flags(session, settings) will
immediately see dashboard changes without a process restart.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_SEED: list[dict] = [
    {
        "key": "shadow_mode_enabled",
        "value": "true",
        "description": "Outbound calls, SMS, and email: true = intercepted (shadow), false = live",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_mode",
        "value": "shadow",
        "description": "GHL write mode: 'shadow' = log-only, 'live' = real API calls",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_shadow_log_only",
        "value": "true",
        "description": "When true, shadow GHL payloads are logged but not persisted to shadow_actions",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_contact_fields",
        "value": "true",
        "description": "Allow GHL contact field updates when ghl_write_mode=live",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_tasks",
        "value": "true",
        "description": "Allow GHL task creation when ghl_write_mode=live",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_summary",
        "value": "true",
        "description": "Allow student summary writeback to GHL when ghl_write_mode=live",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_campaign_state",
        "value": "true",
        "description": "Allow AI Campaign field updates in GHL when ghl_write_mode=live",
        "group": "mode_control",
    },
    {
        "key": "ghl_write_finalization",
        "value": "true",
        "description": "Allow AI Campaign = No finalization writes when ghl_write_mode=live",
        "group": "mode_control",
    },
    {
        "key": "system_paused",
        "value": "false",
        "description": "When true, all workers hold claimed jobs without executing until resumed",
        "group": "mode_control",
    },
]


def upgrade() -> None:
    # ON CONFLICT DO NOTHING — safe to re-run; never overwrites operator changes.
    op.execute(
        sa.text("""
            INSERT INTO app_config (key, value, description, "group", updated_by)
            VALUES
              ('shadow_mode_enabled',       'true',   'Outbound calls, SMS, and email: true = intercepted (shadow), false = live',                'mode_control', 'migration_0015'),
              ('ghl_write_mode',            'shadow', 'GHL write mode: ''shadow'' = log-only, ''live'' = real API calls',                         'mode_control', 'migration_0015'),
              ('ghl_write_shadow_log_only', 'true',   'When true, shadow GHL payloads are logged but not persisted to shadow_actions',            'mode_control', 'migration_0015'),
              ('ghl_write_contact_fields',  'true',   'Allow GHL contact field updates when ghl_write_mode=live',                                 'mode_control', 'migration_0015'),
              ('ghl_write_tasks',           'true',   'Allow GHL task creation when ghl_write_mode=live',                                         'mode_control', 'migration_0015'),
              ('ghl_write_summary',         'true',   'Allow student summary writeback to GHL when ghl_write_mode=live',                          'mode_control', 'migration_0015'),
              ('ghl_write_campaign_state',  'true',   'Allow AI Campaign field updates in GHL when ghl_write_mode=live',                          'mode_control', 'migration_0015'),
              ('ghl_write_finalization',    'true',   'Allow AI Campaign = No finalization writes when ghl_write_mode=live',                      'mode_control', 'migration_0015'),
              ('system_paused',             'false',  'When true, all workers hold claimed jobs without executing until resumed',                  'mode_control', 'migration_0015')
            ON CONFLICT (key) DO NOTHING
        """)
    )


def downgrade() -> None:
    op.execute(
        sa.text("""
            DELETE FROM app_config
            WHERE "group" = 'mode_control'
              AND updated_by = 'migration_0015'
        """)
    )
