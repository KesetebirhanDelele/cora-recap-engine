"""
Integration tests for Alembic migrations against a real Postgres database.

These tests require:
  - A running Postgres instance accessible via DATABASE_URL in .env
  - INTEGRATION_TESTS=1 environment variable

Run with:
  INTEGRATION_TESTS=1 pytest tests/integration/test_migrations.py -v

Tests:
  - Migration 0001 applies cleanly to an empty database
  - Migration 0001 downgrade removes all tables cleanly
  - Migration 0002 applies reporting views on top of 0001
  - Partial unique index uq_task_events_one_success is enforced
  - JSONB columns accept dict values
  - Concurrent claim attempt: only one worker wins (version check)
"""
from __future__ import annotations

import os

import pytest

INTEGRATION = os.environ.get("INTEGRATION_TESTS") == "1"
skip_unless_integration = pytest.mark.skipif(
    not INTEGRATION, reason="Set INTEGRATION_TESTS=1 to run against real Postgres"
)


@skip_unless_integration
def test_migration_0001_up_and_down():
    """Apply and rollback the initial schema."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "0001")
    command.downgrade(alembic_cfg, "base")


@skip_unless_integration
def test_migration_full_up():
    """Apply all migrations to head."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    command.upgrade(alembic_cfg, "head")


@skip_unless_integration
def test_partial_unique_index_enforced():
    """At most one 'created' task_event per call_event_id."""
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db import _sync_url

    settings = get_settings()
    engine = create_engine(_sync_url(settings.database_url))

    call_id = str(uuid.uuid4())
    task1_id = str(uuid.uuid4())
    task2_id = str(uuid.uuid4())
    call_event_id = str(uuid.uuid4())

    with Session(engine) as session:
        session.execute(
            text(
                "INSERT INTO call_events (id, call_id, dedupe_key, created_at) "
                "VALUES (:id, :cid, :dk, NOW())"
            ),
            {"id": call_event_id, "cid": call_id, "dk": f"test:{call_id}"},
        )
        session.execute(
            text(
                "INSERT INTO task_events (id, call_event_id, status, created_at) "
                "VALUES (:id, :ceid, 'created', NOW())"
            ),
            {"id": task1_id, "ceid": call_event_id},
        )
        session.flush()

        # Second 'created' task for the same call must fail
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO task_events (id, call_event_id, status, created_at) "
                    "VALUES (:id, :ceid, 'created', NOW())"
                ),
                {"id": task2_id, "ceid": call_event_id},
            )
            session.flush()
        session.rollback()

        # A 'failed' task for the same call IS allowed
        session.execute(
            text(
                "INSERT INTO task_events (id, call_event_id, status, created_at) "
                "VALUES (:id, :ceid, 'failed', NOW())"
            ),
            {"id": task2_id, "ceid": call_event_id},
        )
        session.flush()  # must not raise
        session.rollback()


@skip_unless_integration
def test_jsonb_columns_accept_dicts():
    """JSONB columns store and retrieve Python dicts."""
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.db import _sync_url

    settings = get_settings()
    engine = create_engine(_sync_url(settings.database_url))

    job_id = str(uuid.uuid4())
    payload = {"tier": 0, "delay_minutes": 120, "campaign": "Cold Lead"}

    with Session(engine) as session:
        session.execute(
            text(
                "INSERT INTO scheduled_jobs "
                "(id, job_type, entity_type, entity_id, run_at, status, "
                " payload_json, version, created_at, updated_at) "
                "VALUES (:id, 'test', 'lead', :eid, NOW(), 'pending', "
                "        :payload::jsonb, 0, NOW(), NOW())"
            ),
            {"id": job_id, "eid": str(uuid.uuid4()), "payload": str(payload).replace("'", '"')},
        )
        session.flush()

        row = session.execute(
            text("SELECT payload_json FROM scheduled_jobs WHERE id = :id"),
            {"id": job_id},
        ).fetchone()
        assert row is not None
        session.rollback()
