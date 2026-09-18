"""Add tag_ai_cold_leads_runs table (spec/31).

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-18

Run-history log for the daily AI cold lead tagging batch job. One row per
cycle (aggregate counts, not per-contact) — mirrors the staff_call_quality
per-unit-of-work precedent rather than forcing this shape into the generic
system_metrics gauge table. Backlog itself is computed live from GHL at
dashboard-read time, not stored here (a stored count would go stale the
moment a new contact starts matching the filter between runs).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tag_ai_cold_leads_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),  # running|completed|failed|skipped
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("contacts_scanned", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_tagged", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_skipped_already_tagged", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_failed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index(
        "idx_tag_ai_cold_leads_runs_started_at", "tag_ai_cold_leads_runs", ["started_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_tag_ai_cold_leads_runs_started_at", table_name="tag_ai_cold_leads_runs")
    op.drop_table("tag_ai_cold_leads_runs")
