"""
Phase 8 — dashboard service tests.

Uses SQLite in-memory — no real Postgres or GHL.

Covers:
  1.  retry_now: creates new scheduled_job, writes audit entry, returns success
  2.  retry_now: exception not open → returns conflict
  3.  retry_now: exception resolved → returns conflict
  4.  retry_with_delay: creates job with correct run_at, writes audit
  5.  retry_with_delay: negative delay rejected
  6.  cancel_future_jobs: cancels pending/claimed jobs, writes audit
  7.  cancel_future_jobs: leaves completed/failed/cancelled jobs untouched
  8.  cancel_future_jobs: returns count of cancelled jobs
  9.  force_finalize: resolves exception, cancels jobs, writes audit
  10. force_finalize: exception already resolved → conflict
  11. force_finalize: concurrent force_finalize → conflict on second
  12. list_exceptions: returns all exceptions
  13. list_exceptions: filters by status
  14. list_exceptions: filters by severity
  15. list_exceptions: search by entity_id
  16. get_exception_detail: returns exception with audit trail
  17. get_exception_detail: returns None for unknown id
  18. audit_log: every action writes a row with correct action name
  19. audit_log: each row has entity_type='exception' and entity_id
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import AuditLog, Base, ExceptionRecord, ScheduledJob
from app.services.dashboard import (
    cancel_future_jobs,
    force_finalize,
    get_exception_detail,
    list_exceptions,
    retry_now,
    retry_with_delay,
)
from app.worker.exceptions import create_exception

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_exception(session, **kwargs) -> ExceptionRecord:
    defaults = dict(type="ghl_auth", context={"job_type": "process_call_event"},
                    severity="critical", entity_type="call", entity_id=str(uuid.uuid4()))
    defaults.update(kwargs)
    exc = create_exception(session, **defaults)
    return exc


def _make_job(session, entity_id, status="pending") -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="process_call_event",
        entity_type="call", entity_id=entity_id,
        run_at=_now(), status=status, version=0,
        created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()
    return job


# ─────────────────────────────────────────────────────────────────────────────
# 1-3. retry_now
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_now_returns_success(session):
    exc = _make_exception(session)
    session.flush()
    result = retry_now(session, exc.id, operator_id="ops-1")
    assert result["success"] is True
    assert "new_job_id" in result


def test_retry_now_creates_scheduled_job(session):
    exc = _make_exception(session)
    session.flush()
    result = retry_now(session, exc.id, operator_id="ops-1")
    job = session.get(ScheduledJob, result["new_job_id"])
    assert job is not None
    assert job.status == "pending"


def test_retry_now_writes_audit_entry(session):
    exc = _make_exception(session)
    session.flush()
    retry_now(session, exc.id, operator_id="ops-audit")
    audit = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == exc.id, AuditLog.action == "retry_now")
    ).first()
    assert audit is not None
    assert audit.operator_id == "ops-audit"


def test_retry_now_conflict_when_not_open(session):
    exc = _make_exception(session)
    session.flush()
    from app.worker.exceptions import resolve_exception
    resolve_exception(session, exc.id, resolved_by="ops", reason="done")
    result = retry_now(session, exc.id, operator_id="ops-1")
    assert result.get("conflict") is True


def test_retry_now_conflict_for_nonexistent(session):
    result = retry_now(session, str(uuid.uuid4()), operator_id="ops-1")
    assert result.get("conflict") is True


# ─────────────────────────────────────────────────────────────────────────────
# 4-5. retry_with_delay
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_with_delay_creates_future_job(session):
    exc = _make_exception(session)
    session.flush()
    result = retry_with_delay(session, exc.id, operator_id="ops", delay_minutes=60)
    assert result["success"] is True
    assert "run_at" in result
    job = session.get(ScheduledJob, result["new_job_id"])
    assert job is not None
    assert job.status == "pending"


def test_retry_with_delay_negative_returns_error(session):
    exc = _make_exception(session)
    session.flush()
    result = retry_with_delay(session, exc.id, operator_id="ops", delay_minutes=-1)
    assert "error" in result


def test_retry_with_delay_writes_audit(session):
    exc = _make_exception(session)
    session.flush()
    retry_with_delay(session, exc.id, operator_id="ops-delay", delay_minutes=30)
    audit = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == exc.id, AuditLog.action == "retry_delay")
    ).first()
    assert audit is not None
    assert audit.context_json.get("delay_minutes") == 30


# ─────────────────────────────────────────────────────────────────────────────
# 6-8. cancel_future_jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_cancel_future_jobs_cancels_pending(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    job = _make_job(session, entity_id, status="pending")
    session.flush()

    result = cancel_future_jobs(session, exc.id, operator_id="ops")
    assert result["success"] is True
    assert result["cancelled_count"] == 1
    session.refresh(job)
    assert job.status == "cancelled"


def test_cancel_future_jobs_leaves_completed_untouched(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    job = _make_job(session, entity_id, status="completed")
    session.flush()

    result = cancel_future_jobs(session, exc.id, operator_id="ops")
    assert result["cancelled_count"] == 0
    session.refresh(job)
    assert job.status == "completed"


def test_cancel_future_jobs_returns_count(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    for _ in range(3):
        _make_job(session, entity_id, status="pending")
    session.flush()

    result = cancel_future_jobs(session, exc.id, operator_id="ops")
    assert result["cancelled_count"] == 3


def test_cancel_future_jobs_writes_audit(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    cancel_future_jobs(session, exc.id, operator_id="ops-cancel")
    audit = session.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == exc.id,
            AuditLog.action == "cancel_future_jobs",
        )
    ).first()
    assert audit is not None
    assert audit.operator_id == "ops-cancel"


# ─────────────────────────────────────────────────────────────────────────────
# 9-11. force_finalize
# ─────────────────────────────────────────────────────────────────────────────

def test_force_finalize_returns_success(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    result = force_finalize(session, exc.id, operator_id="ops")
    assert result["success"] is True


def test_force_finalize_resolves_exception(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    force_finalize(session, exc.id, operator_id="ops")
    session.refresh(exc)
    assert exc.status == "resolved"
    assert exc.resolution_reason == "force_finalized"


def test_force_finalize_already_resolved_returns_conflict(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    from app.worker.exceptions import resolve_exception
    resolve_exception(session, exc.id, resolved_by="ops", reason="manual")
    result = force_finalize(session, exc.id, operator_id="ops")
    assert result.get("conflict") is True


def test_force_finalize_concurrent_second_fails(session):
    """First force_finalize resolves the exception; second gets a conflict."""
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    first = force_finalize(session, exc.id, operator_id="ops-a")
    second = force_finalize(session, exc.id, operator_id="ops-b")
    assert first["success"] is True
    assert second.get("conflict") is True


def test_force_finalize_cancels_pending_jobs(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    job = _make_job(session, entity_id, status="pending")
    session.flush()

    result = force_finalize(session, exc.id, operator_id="ops")
    assert result["cancelled_jobs"] == 1
    session.refresh(job)
    assert job.status == "cancelled"


def test_force_finalize_writes_audit(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    force_finalize(session, exc.id, operator_id="ops-ff")
    audit = session.scalars(
        select(AuditLog).where(
            AuditLog.entity_id == exc.id,
            AuditLog.action == "force_finalize",
        )
    ).first()
    assert audit is not None
    assert audit.operator_id == "ops-ff"


# ─────────────────────────────────────────────────────────────────────────────
# 12-15. list_exceptions
# ─────────────────────────────────────────────────────────────────────────────

def test_list_exceptions_returns_dict_with_exceptions_key(session):
    result = list_exceptions(session)
    assert "exceptions" in result
    assert "total" in result


def test_list_exceptions_filters_by_status(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    open_result = list_exceptions(session, status="open")
    assert any(e["id"] == exc.id for e in open_result["exceptions"])

    resolved_result = list_exceptions(session, status="resolved")
    assert not any(e["id"] == exc.id for e in resolved_result["exceptions"])


def test_list_exceptions_filters_by_severity(session):
    exc = _make_exception(session, severity="warning", entity_id=str(uuid.uuid4()))
    session.flush()

    result = list_exceptions(session, severity="warning")
    assert any(e["id"] == exc.id for e in result["exceptions"])


def test_list_exceptions_search_by_entity_id(session):
    entity_id = f"search-target-{uuid.uuid4()}"
    exc = _make_exception(session, entity_id=entity_id)
    session.flush()

    result = list_exceptions(session, search=entity_id[:20])
    assert any(e["id"] == exc.id for e in result["exceptions"])


# ─────────────────────────────────────────────────────────────────────────────
# 16-17. get_exception_detail
# ─────────────────────────────────────────────────────────────────────────────

def test_get_exception_detail_returns_dict(session):
    exc = _make_exception(session, entity_id=str(uuid.uuid4()))
    session.flush()

    result = get_exception_detail(session, exc.id)
    assert result is not None
    assert result["id"] == exc.id


def test_get_exception_detail_includes_audit_trail(session):
    exc = _make_exception(session, entity_id=str(uuid.uuid4()))
    session.flush()
    retry_now(session, exc.id, operator_id="ops-audit-check")

    result = get_exception_detail(session, exc.id)
    assert "audit_trail" in result
    assert len(result["audit_trail"]) >= 1
    assert result["audit_trail"][0]["action"] == "retry_now"


def test_get_exception_detail_returns_none_for_unknown(session):
    result = get_exception_detail(session, str(uuid.uuid4()))
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 18-19. Audit log invariants
# ─────────────────────────────────────────────────────────────────────────────

def test_every_action_writes_audit(session):
    entity_id = str(uuid.uuid4())
    exc = _make_exception(session, entity_type="call", entity_id=entity_id)
    session.flush()

    retry_now(session, exc.id, "op1")
    retry_with_delay(session, exc.id, "op2", 30)
    cancel_future_jobs(session, exc.id, "op3")

    audits = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == exc.id)
    ).all()
    actions = {a.action for a in audits}
    assert "retry_now" in actions
    assert "retry_delay" in actions
    assert "cancel_future_jobs" in actions


def test_audit_rows_have_correct_entity_type(session):
    exc = _make_exception(session, entity_id=str(uuid.uuid4()))
    session.flush()
    retry_now(session, exc.id, "ops")

    audits = session.scalars(
        select(AuditLog).where(AuditLog.entity_id == exc.id)
    ).all()
    for a in audits:
        assert a.entity_type == "exception"
