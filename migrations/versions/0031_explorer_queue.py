"""Add explorer_queue_runs and explorer_queue_contacts (spec/49).

explorer_queue_runs      nightly run ledger (aggregate counts), like tag_ai_cold_leads_runs.
explorer_queue_contacts  per-contact state: when the queue tag was written (drain rule),
                         last values written (write-only-when-changed), when drained.

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "explorer_queue_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),  # running|completed|failed|skipped
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.Column("view", sa.String(20), nullable=True),
        sa.Column("rows_fetched", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_tagged", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_already_tagged", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_untagged", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_no_ghl_url", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_ai_campaign", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("skipped_no_access", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("contacts_failed", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("idx_explorer_queue_runs_started_at", "explorer_queue_runs", ["started_at"])

    op.create_table(
        "explorer_queue_contacts",
        sa.Column("ghl_contact_id", sa.String(64), primary_key=True),
        sa.Column("ghl_location_id", sa.String(64), nullable=False),
        sa.Column("enrollment_id", sa.String(64), nullable=True),
        sa.Column("tagged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("drained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_stage_tag", sa.String(40), nullable=True),
        sa.Column("last_score_band_tag", sa.String(40), nullable=True),
        sa.Column("last_score", sa.Float, nullable=True),
        sa.Column("last_summary_hash", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_explorer_queue_contacts_drained_at", "explorer_queue_contacts", ["drained_at"])


def downgrade() -> None:
    op.drop_index("idx_explorer_queue_contacts_drained_at", table_name="explorer_queue_contacts")
    op.drop_table("explorer_queue_contacts")
    op.drop_index("idx_explorer_queue_runs_started_at", table_name="explorer_queue_runs")
    op.drop_table("explorer_queue_runs")
