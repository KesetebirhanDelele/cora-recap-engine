"""Reporting views — Postgres-authoritative reporting layer.

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-09

Views created:
  fact_call_activity  — one row per call event, denormalized for KPI queries
  fact_kpi_daily      — daily aggregated KPIs for dashboard KPI cards

Dashboard metrics must be computed from these views, not from Google Sheets.
Sheets shadow data (shadow_sheet_rows) must not be used as the runtime
source for KPI calculations.

KPIs supported:
  - Unique Contacts
  - Booked Appts (task_events with status='created')
  - Calls Per Day
  - Call Completion Rate (completed / total)
  - Call Duration in Sec.
  - Pickup Rate (completed / total)
  - Voicemail Rate (voicemail / total)
  - Failed Rate (failed / total)

Filtering dimensions:
  date range, call direction, call status (type), campaign_name
"""
from __future__ import annotations

from alembic import op

revision: str = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── fact_call_activity ────────────────────────────────────────────────────
    # One row per call event. Joins to summary_results and task_events for
    # consent and task existence flags. Joins to lead_state for campaign context.
    op.execute(
        """
        CREATE OR REPLACE VIEW fact_call_activity AS
        SELECT
            ce.id                                           AS id,
            ce.call_id                                      AS call_id,
            ce.contact_id                                   AS contact_id,
            ce.direction                                    AS direction,
            ce.status                                       AS call_status,
            ce.end_call_reason                              AS end_call_reason,
            ce.duration_seconds                             AS duration_seconds,
            ce.start_time_utc                               AS start_time_utc,
            DATE(ce.start_time_utc AT TIME ZONE 'UTC')      AS call_date,
            ce.created_at                                   AS created_at,
            sr.summary_consent                              AS summary_consent,
            (te.id IS NOT NULL)                             AS has_task,
            te.provider_task_id                             AS provider_task_id,
            ls.lead_stage                                   AS lead_stage,
            ls.campaign_name                                AS campaign_name,
            ls.ai_campaign_value                            AS ai_campaign_value
        FROM call_events ce
        LEFT JOIN summary_results sr
            ON sr.call_event_id = ce.id
        LEFT JOIN task_events te
            ON te.call_event_id = ce.id
            AND te.status = 'created'
        LEFT JOIN lead_state ls
            ON ls.contact_id = ce.contact_id
        """
    )

    # ── fact_kpi_daily ────────────────────────────────────────────────────────
    # Daily aggregated KPIs. Dashboard date-range filters apply WHERE call_date
    # BETWEEN :start AND :end against this view.
    op.execute(
        """
        CREATE OR REPLACE VIEW fact_kpi_daily AS
        SELECT
            call_date,
            direction,
            campaign_name,
            COUNT(DISTINCT contact_id)                          AS unique_contacts,
            COUNT(*)                                            AS total_calls,
            COUNT(*) FILTER (WHERE call_status = 'completed')  AS completed_calls,
            COUNT(*) FILTER (WHERE call_status IN (
                'voicemail', 'hangup_on_voicemail'))            AS voicemail_calls,
            COUNT(*) FILTER (WHERE call_status = 'failed')     AS failed_calls,
            COUNT(*) FILTER (WHERE has_task = TRUE)            AS booked_appts,
            AVG(duration_seconds)
                FILTER (WHERE call_status = 'completed')       AS avg_duration_seconds,
            CASE
                WHEN COUNT(*) > 0
                THEN COUNT(*) FILTER (WHERE call_status = 'completed')::FLOAT / COUNT(*)
                ELSE 0
            END                                                 AS completion_rate,
            CASE
                WHEN COUNT(*) > 0
                THEN COUNT(*) FILTER (WHERE call_status IN (
                    'voicemail', 'hangup_on_voicemail'))::FLOAT / COUNT(*)
                ELSE 0
            END                                                 AS voicemail_rate,
            CASE
                WHEN COUNT(*) > 0
                THEN COUNT(*) FILTER (WHERE call_status = 'failed')::FLOAT / COUNT(*)
                ELSE 0
            END                                                 AS failed_rate
        FROM fact_call_activity
        GROUP BY call_date, direction, campaign_name
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS fact_kpi_daily")
    op.execute("DROP VIEW IF EXISTS fact_call_activity")
