"""Initial schema — all authoritative tables.

Revision ID: 0001
Revises:
Create Date: 2026-03-09

Tables created:
  lead_state            — campaign state per GHL contact
  call_events           — idempotent call event records
  classification_results — AI analysis outputs
  summary_results       — student summary and consent gate result
  task_events           — GHL task creation records
  scheduled_jobs        — durable canonical job state
  shadow_sheet_rows     — Sheets mirror data (shadow mode)
  exceptions            — surfaced failures

Unique constraints encode the idempotency rules from the autonomous
execution contract (spec/12 §5.3).

Partial unique indexes (Postgres-only):
  uq_task_events_one_success — at most one 'created' task per call event
These cannot be represented in SQLAlchemy model metadata directly and are
created via op.execute() here.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── lead_state ────────────────────────────────────────────────────────────
    op.create_table(
        "lead_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("contact_id", sa.String(255), nullable=False),
        sa.Column("normalized_phone", sa.String(20)),
        sa.Column("lead_stage", sa.String(100)),
        sa.Column("campaign_name", sa.String(100)),
        sa.Column("ai_campaign", sa.String(10)),
        sa.Column("ai_campaign_value", sa.String(10)),
        sa.Column("last_call_status", sa.String(50)),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("contact_id", name="uq_lead_state_contact_id"),
    )
    op.create_index("idx_lead_state_phone", "lead_state", ["normalized_phone"])

    # ── call_events ───────────────────────────────────────────────────────────
    op.create_table(
        "call_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("call_id", sa.String(255), nullable=False),
        sa.Column("contact_id", sa.String(255)),
        sa.Column("direction", sa.String(20)),
        sa.Column("status", sa.String(50)),
        sa.Column("end_call_reason", sa.String(100)),
        sa.Column("transcript", sa.Text),
        sa.Column("duration_seconds", sa.Integer),
        sa.Column("recording_url", sa.Text),
        sa.Column("start_time_utc", sa.DateTime(timezone=True)),
        sa.Column("dedupe_key", sa.String(512), nullable=False),
        sa.Column("raw_payload_json", sa.dialects.postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_call_events_dedupe_key"),
    )
    op.create_index("idx_call_events_call_id", "call_events", ["call_id"])
    op.create_index("idx_call_events_created_at", "call_events", ["created_at"])
    op.create_index("idx_call_events_contact_id", "call_events", ["contact_id"])

    # ── classification_results ────────────────────────────────────────────────
    op.create_table(
        "classification_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "call_event_id",
            sa.String(36),
            sa.ForeignKey("call_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_used", sa.String(100)),
        sa.Column("prompt_family", sa.String(100)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column("output_json", sa.dialects.postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_classification_call_event", "classification_results", ["call_event_id"]
    )

    # ── summary_results ───────────────────────────────────────────────────────
    op.create_table(
        "summary_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "call_event_id",
            sa.String(36),
            sa.ForeignKey("call_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("student_summary", sa.Text),
        sa.Column("summary_offered", sa.Boolean),
        sa.Column("summary_consent", sa.String(20)),
        sa.Column("model_used", sa.String(100)),
        sa.Column("prompt_family", sa.String(100)),
        sa.Column("prompt_version", sa.String(50)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("call_event_id", name="uq_summary_results_call_event"),
    )

    # ── task_events ───────────────────────────────────────────────────────────
    op.create_table(
        "task_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "call_event_id",
            sa.String(36),
            sa.ForeignKey("call_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_task_id", sa.String(255)),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_task_events_call_event", "task_events", ["call_event_id"])
    op.create_index(
        "idx_task_events_status", "task_events", ["call_event_id", "status"]
    )
    # Partial unique index: at most one 'created' task per call event
    op.execute(
        """
        CREATE UNIQUE INDEX uq_task_events_one_success
            ON task_events(call_event_id)
            WHERE status = 'created'
        """
    )

    # ── scheduled_jobs ────────────────────────────────────────────────────────
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rq_job_id", sa.String(255)),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(255)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("payload_json", sa.dialects.postgresql.JSONB),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_scheduled_jobs_entity",
        "scheduled_jobs",
        ["entity_type", "entity_id", "status"],
    )
    op.create_index(
        "idx_scheduled_jobs_run_at", "scheduled_jobs", ["run_at", "status"]
    )
    op.create_index(
        "idx_scheduled_jobs_status", "scheduled_jobs", ["status", "run_at"]
    )

    # ── shadow_sheet_rows ─────────────────────────────────────────────────────
    op.create_table(
        "shadow_sheet_rows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sheet_name", sa.String(255), nullable=False),
        sa.Column("source_row_id", sa.String(255), nullable=False),
        sa.Column("payload_json", sa.dialects.postgresql.JSONB),
        sa.Column(
            "mirrored_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "reconciliation_status",
            sa.String(50),
            nullable=False,
            server_default="pending",
        ),
        sa.UniqueConstraint(
            "sheet_name", "source_row_id", name="uq_shadow_sheet_rows_identity"
        ),
    )
    op.create_index(
        "idx_shadow_sheet_name_status",
        "shadow_sheet_rows",
        ["sheet_name", "reconciliation_status"],
    )

    # ── exceptions ────────────────────────────────────────────────────────────
    op.create_table(
        "exceptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "call_event_id",
            sa.String(36),
            sa.ForeignKey("call_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("entity_type", sa.String(50)),
        sa.Column("entity_id", sa.String(255)),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="critical"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution_reason", sa.String(500)),
        sa.Column("resolved_by", sa.String(255)),
        sa.Column("context_json", sa.dialects.postgresql.JSONB),
        sa.Column("version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_exceptions_status", "exceptions", ["status", "created_at"])
    op.create_index("idx_exceptions_call_event", "exceptions", ["call_event_id"])
    op.create_index("idx_exceptions_entity", "exceptions", ["entity_type", "entity_id"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("exceptions")
    op.drop_table("shadow_sheet_rows")
    op.drop_table("scheduled_jobs")
    op.drop_table("task_events")
    op.drop_table("summary_results")
    op.drop_table("classification_results")
    op.drop_table("call_events")
    op.drop_table("lead_state")
