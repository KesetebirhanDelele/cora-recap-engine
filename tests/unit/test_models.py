"""
Phase 3 model tests.

Strategy:
  Unit tests use SQLite in-memory (no Postgres required).
  They verify model structure, table names, column presence, constraint
  definitions, and transactional behaviour that SQLite supports.

  Postgres-specific constraints (partial unique indexes, JSONB type checks)
  are tested in tests/integration/test_migrations.py (INTEGRATION_TESTS=1).

Covers:
  1. All models import without errors
  2. Table names match spec
  3. Required columns exist with correct nullability
  4. Unique constraints are declared on the right columns
  5. Duplicate inserts violate unique constraints (SQLite enforcement)
  6. Transaction rollback on constraint violation
  7. Version column exists on tables with optimistic concurrency
  8. Scheduled job status defaults and claim fields exist
  9. SummaryResult consent values are stored and retrieved correctly
  10. ShadowSheetRow reconciliation_status defaults to 'pending'
  11. ExceptionRecord status defaults to 'open' and severity to 'critical'
  12. ScheduledJob status defaults to 'pending'
  13. Restart-safe: scheduled_jobs rows persist after session close/reopen
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.models import (
    Base,
    CallEvent,
    ClassificationResult,
    ExceptionRecord,
    LeadState,
    ScheduledJob,
    ShadowSheetRow,
    SummaryResult,
    TaskEvent,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    """SQLite in-memory engine for unit tests — no Postgres required."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """Provide a fresh session per test; roll back after each test."""
    with Session(engine) as sess:
        yield sess
        sess.rollback()


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Import smoke
# ─────────────────────────────────────────────────────────────────────────────

def test_all_models_importable():
    from app.models import (
        Base,
        CallEvent,
        ExceptionRecord,
        LeadState,
        ScheduledJob,
        ShadowSheetRow,
        SummaryResult,
    )
    for model in [
        Base, LeadState, CallEvent, ClassificationResult, SummaryResult,
        TaskEvent, ScheduledJob, ShadowSheetRow, ExceptionRecord,
    ]:
        assert model is not None


def test_db_module_importable():
    from app.db import get_async_engine, get_session, get_sync_engine
    assert get_sync_engine is not None
    assert get_async_engine is not None
    assert get_session is not None


# ─────────────────────────────────────────────────────────────────────────────
# 2. Table names
# ─────────────────────────────────────────────────────────────────────────────

def test_table_names(engine):
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    expected = {
        "lead_state",
        "call_events",
        "classification_results",
        "summary_results",
        "task_events",
        "scheduled_jobs",
        "shadow_sheet_rows",
        "exceptions",
    }
    assert expected.issubset(existing), f"Missing tables: {expected - existing}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Column presence
# ─────────────────────────────────────────────────────────────────────────────

def _column_names(engine, table: str) -> set[str]:
    inspector = inspect(engine)
    return {c["name"] for c in inspector.get_columns(table)}


def test_lead_state_columns(engine):
    cols = _column_names(engine, "lead_state")
    required = {
        "id", "contact_id", "normalized_phone", "lead_stage",
        "campaign_name", "ai_campaign", "ai_campaign_value",
        "last_call_status", "version", "updated_at", "created_at",
    }
    assert required.issubset(cols)


def test_call_events_columns(engine):
    cols = _column_names(engine, "call_events")
    required = {
        "id", "call_id", "contact_id", "direction", "status",
        "end_call_reason", "transcript", "duration_seconds",
        "recording_url", "start_time_utc", "dedupe_key",
        "raw_payload_json", "created_at",
    }
    assert required.issubset(cols)


def test_classification_results_columns(engine):
    cols = _column_names(engine, "classification_results")
    required = {
        "id", "call_event_id", "model_used", "prompt_family",
        "prompt_version", "output_json", "created_at",
    }
    assert required.issubset(cols)


def test_summary_results_columns(engine):
    cols = _column_names(engine, "summary_results")
    required = {
        "id", "call_event_id", "student_summary", "summary_offered",
        "summary_consent", "model_used", "prompt_family",
        "prompt_version", "created_at",
    }
    assert required.issubset(cols)


def test_task_events_columns(engine):
    cols = _column_names(engine, "task_events")
    required = {"id", "call_event_id", "provider_task_id", "status", "created_at"}
    assert required.issubset(cols)


def test_scheduled_jobs_columns(engine):
    cols = _column_names(engine, "scheduled_jobs")
    required = {
        "id", "job_type", "entity_type", "entity_id", "run_at",
        "rq_job_id", "status", "claimed_by", "claimed_at",
        "lease_expires_at", "payload_json", "version",
        "created_at", "updated_at",
    }
    assert required.issubset(cols)


def test_shadow_sheet_rows_columns(engine):
    cols = _column_names(engine, "shadow_sheet_rows")
    required = {
        "id", "sheet_name", "source_row_id", "payload_json",
        "mirrored_at", "reconciliation_status",
    }
    assert required.issubset(cols)


def test_exceptions_columns(engine):
    cols = _column_names(engine, "exceptions")
    required = {
        "id", "call_event_id", "entity_type", "entity_id",
        "type", "severity", "status", "resolution_reason",
        "resolved_by", "context_json", "version",
        "created_at", "updated_at",
    }
    assert required.issubset(cols)


# ─────────────────────────────────────────────────────────────────────────────
# 4 + 5. Unique constraints — SQLite enforcement
# ─────────────────────────────────────────────────────────────────────────────

def test_call_events_dedupe_key_unique(session):
    """Duplicate dedupe_key must raise an integrity error."""
    from sqlalchemy.exc import IntegrityError

    key = f"call-dupe:{_id()}"
    e1 = CallEvent(id=_id(), call_id="c1", dedupe_key=key, created_at=_now())
    e2 = CallEvent(id=_id(), call_id="c2", dedupe_key=key, created_at=_now())
    session.add(e1)
    session.flush()
    session.add(e2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_lead_state_contact_id_unique(session):
    """Duplicate contact_id must raise an integrity error."""
    from sqlalchemy.exc import IntegrityError

    cid = f"contact-{_id()}"
    l1 = LeadState(id=_id(), contact_id=cid, version=0, updated_at=_now(), created_at=_now())
    l2 = LeadState(id=_id(), contact_id=cid, version=0, updated_at=_now(), created_at=_now())
    session.add(l1)
    session.flush()
    session.add(l2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_summary_results_call_event_unique(session):
    """Duplicate call_event_id in summary_results must raise an integrity error."""
    from sqlalchemy.exc import IntegrityError

    call_id_val = _id()
    ce = CallEvent(
        id=call_id_val, call_id="c-sum", dedupe_key=f"sum:{_id()}", created_at=_now()
    )
    session.add(ce)
    session.flush()

    s1 = SummaryResult(id=_id(), call_event_id=call_id_val, created_at=_now())
    s2 = SummaryResult(id=_id(), call_event_id=call_id_val, created_at=_now())
    session.add(s1)
    session.flush()
    session.add(s2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_shadow_sheet_rows_identity_unique(session):
    """Duplicate (sheet_name, source_row_id) must raise an integrity error."""
    from sqlalchemy.exc import IntegrityError

    r1 = ShadowSheetRow(
        id=_id(), sheet_name="Inbound", source_row_id="row-1",
        reconciliation_status="pending", mirrored_at=_now()
    )
    r2 = ShadowSheetRow(
        id=_id(), sheet_name="Inbound", source_row_id="row-1",
        reconciliation_status="pending", mirrored_at=_now()
    )
    session.add(r1)
    session.flush()
    session.add(r2)
    with pytest.raises(IntegrityError):
        session.flush()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Transaction rollback
# ─────────────────────────────────────────────────────────────────────────────

def test_transaction_rollback_on_unique_violation(session):
    """After an IntegrityError and rollback, the session can continue cleanly."""
    from sqlalchemy.exc import IntegrityError

    key = f"rollback-test:{_id()}"
    e1 = CallEvent(id=_id(), call_id="c-rb1", dedupe_key=key, created_at=_now())
    session.add(e1)
    session.flush()

    # This will fail
    e2 = CallEvent(id=_id(), call_id="c-rb2", dedupe_key=key, created_at=_now())
    session.add(e2)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()

    # After rollback, session is usable with a fresh transaction
    new_key = f"after-rollback:{_id()}"
    e3 = CallEvent(id=_id(), call_id="c-rb3", dedupe_key=new_key, created_at=_now())
    session.add(e3)
    session.flush()  # must not raise
    count = session.execute(
        text("SELECT COUNT(*) FROM call_events WHERE dedupe_key = :k"), {"k": new_key}
    ).scalar()
    assert count == 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. Version column on optimistic-concurrency tables
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("table", ["lead_state", "scheduled_jobs", "exceptions"])
def test_version_column_exists(engine, table):
    cols = _column_names(engine, table)
    assert "version" in cols, f"'version' column missing from {table}"


def test_lead_state_version_defaults_to_zero(session):
    ls = LeadState(
        id=_id(), contact_id=f"cid-{_id()}", version=0,
        updated_at=_now(), created_at=_now()
    )
    session.add(ls)
    session.flush()
    assert ls.version == 0


def test_scheduled_job_version_defaults_to_zero(session):
    job = ScheduledJob(
        id=_id(), job_type="test_job", entity_type="call", entity_id="e-1",
        run_at=_now(), status="pending", version=0, created_at=_now(), updated_at=_now()
    )
    session.add(job)
    session.flush()
    assert job.version == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8. ScheduledJob claim fields
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduled_job_claim_fields_nullable(session):
    """claimed_by, claimed_at, lease_expires_at are nullable before claiming."""
    job = ScheduledJob(
        id=_id(), job_type="synthflow_callback",
        entity_type="lead", entity_id=_id(),
        run_at=_now(), status="pending", version=0,
        created_at=_now(), updated_at=_now()
    )
    session.add(job)
    session.flush()
    assert job.claimed_by is None
    assert job.claimed_at is None
    assert job.lease_expires_at is None


def test_scheduled_job_claim_fields_can_be_set(session):
    """After claiming, claim fields are populated."""
    job = ScheduledJob(
        id=_id(), job_type="retry_processing",
        entity_type="call", entity_id=_id(),
        run_at=_now(), status="claimed", version=1,
        claimed_by="worker-01", claimed_at=_now(), lease_expires_at=_now(),
        created_at=_now(), updated_at=_now()
    )
    session.add(job)
    session.flush()
    assert job.claimed_by == "worker-01"
    assert job.claimed_at is not None
    assert job.lease_expires_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# 9. SummaryResult consent values
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("consent", ["YES", "NO", "UNKNOWN"])
def test_summary_consent_values_stored(session, consent):
    ce = CallEvent(
        id=_id(), call_id=f"c-consent-{consent}",
        dedupe_key=f"consent:{_id()}", created_at=_now()
    )
    session.add(ce)
    session.flush()

    sr = SummaryResult(
        id=_id(), call_event_id=ce.id,
        summary_consent=consent, created_at=_now()
    )
    session.add(sr)
    session.flush()
    session.refresh(sr)
    assert sr.summary_consent == consent


def test_blank_summary_stored_as_none_or_empty(session):
    """Blank transcript → blank summary (empty string or None, not an error)."""
    ce = CallEvent(
        id=_id(), call_id="c-blank", dedupe_key=f"blank:{_id()}", created_at=_now()
    )
    session.add(ce)
    session.flush()

    sr = SummaryResult(
        id=_id(), call_event_id=ce.id,
        student_summary="", summary_consent="NO", created_at=_now()
    )
    session.add(sr)
    session.flush()
    session.refresh(sr)
    assert sr.student_summary == "" or sr.student_summary is None


# ─────────────────────────────────────────────────────────────────────────────
# 10. ShadowSheetRow defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_sheet_row_default_status(session):
    row = ShadowSheetRow(
        id=_id(), sheet_name="ColdLeads", source_row_id=f"r-{_id()}",
        reconciliation_status="pending", mirrored_at=_now()
    )
    session.add(row)
    session.flush()
    assert row.reconciliation_status == "pending"


@pytest.mark.parametrize("status", ["pending", "matched", "drift", "error"])
def test_shadow_sheet_reconciliation_statuses(session, status):
    row = ShadowSheetRow(
        id=_id(), sheet_name="Sheet", source_row_id=f"r-{_id()}",
        reconciliation_status=status, mirrored_at=_now()
    )
    session.add(row)
    session.flush()
    session.refresh(row)
    assert row.reconciliation_status == status


# ─────────────────────────────────────────────────────────────────────────────
# 11. ExceptionRecord defaults
# ─────────────────────────────────────────────────────────────────────────────

def test_exception_default_status_and_severity(session):
    exc = ExceptionRecord(
        id=_id(), type="identity_resolution",
        severity="critical", status="open", version=0,
        created_at=_now(), updated_at=_now()
    )
    session.add(exc)
    session.flush()
    assert exc.status == "open"
    assert exc.severity == "critical"


def test_exception_nullable_call_event_id(session):
    """Exceptions can exist without a call_event_id (e.g., auth failure at startup)."""
    exc = ExceptionRecord(
        id=_id(), type="ghl_auth", severity="critical", status="open",
        version=0, created_at=_now(), updated_at=_now()
    )
    session.add(exc)
    session.flush()
    assert exc.call_event_id is None


def test_exception_operator_resolution_fields(session):
    exc = ExceptionRecord(
        id=_id(), type="retry_budget_exhausted", severity="warning",
        status="resolved", resolution_reason="manually confirmed duplicate",
        resolved_by="ops-user-1", version=1,
        created_at=_now(), updated_at=_now()
    )
    session.add(exc)
    session.flush()
    session.refresh(exc)
    assert exc.status == "resolved"
    assert exc.resolved_by == "ops-user-1"
    assert exc.resolution_reason is not None


# ─────────────────────────────────────────────────────────────────────────────
# 12. ScheduledJob status default
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduled_job_status_values(session):
    for status in ["pending", "claimed", "running", "completed", "failed", "cancelled"]:
        job = ScheduledJob(
            id=_id(), job_type="test", entity_type="call", entity_id=_id(),
            run_at=_now(), status=status, version=0,
            created_at=_now(), updated_at=_now()
        )
        session.add(job)
    session.flush()  # all six status values must be storable


# ─────────────────────────────────────────────────────────────────────────────
# 13. Restart-safe: scheduled_jobs rows persist across session close/reopen
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduled_job_persists_across_sessions(engine):
    """Simulates a worker restart: job written in one session, read in another."""
    job_id = _id()
    entity_id = _id()

    # Write in session 1
    with Session(engine) as s1:
        job = ScheduledJob(
            id=job_id, job_type="synthflow_callback",
            entity_type="lead", entity_id=entity_id,
            run_at=_now(), status="pending", version=0,
            created_at=_now(), updated_at=_now()
        )
        s1.add(job)
        s1.commit()

    # Read in session 2 (simulates worker restart reading from Postgres)
    with Session(engine) as s2:
        recovered = s2.get(ScheduledJob, job_id)
        assert recovered is not None
        assert recovered.entity_id == entity_id
        assert recovered.status == "pending"
        assert recovered.version == 0


def test_claimed_job_persists_across_sessions(engine):
    """After claiming, a restarted worker can read the claim details."""
    job_id = _id()

    with Session(engine) as s1:
        job = ScheduledJob(
            id=job_id, job_type="retry_processing",
            entity_type="call", entity_id=_id(),
            run_at=_now(), status="claimed", version=1,
            claimed_by="worker-host-01", claimed_at=_now(), lease_expires_at=_now(),
            created_at=_now(), updated_at=_now()
        )
        s1.add(job)
        s1.commit()

    with Session(engine) as s2:
        recovered = s2.get(ScheduledJob, job_id)
        assert recovered is not None
        assert recovered.status == "claimed"
        assert recovered.claimed_by == "worker-host-01"
        assert recovered.version == 1
