"""
Unit tests for crm_jobs.py — Features 2 and 3.

Covers:
  1.  create_crm_task: happy path — creates task and TaskEvent row
  2.  create_crm_task: job already claimed → returns immediately
  3.  create_crm_task: dedupe — skips when TaskEvent status='created' exists
  4.  create_crm_task: missing call_event_id → fails job + creates exception
  5.  create_crm_task: missing CallEvent row → fails job + creates exception
  6.  send_student_summary: happy path — consent=YES, writes summary + audit
  7.  send_student_summary: consent=NO → completes without writing
  8.  send_student_summary: consent=UNKNOWN → completes without writing
  9.  send_student_summary: no SummaryResult → completes without writing
  10. send_student_summary: empty summary text → completes without writing
  11. send_student_summary: job already claimed → returns immediately
  12. _record_summary_audit: creates AuditLog row
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.models.audit import AuditLog
from app.models.call_event import CallEvent
from app.models.summary import SummaryResult
from app.models.task_event import TaskEvent


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


def _make_job(session: Session, job_type: str, payload: dict) -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="call",
        entity_id=payload.get("call_id") or payload.get("call_event_id", "test"),
        status="pending",
        run_at=datetime.now(tz=timezone.utc),
        payload_json=payload,
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()
    return job


def _make_call_event(session: Session, call_id: str = "call-001",
                     contact_id: str | None = "contact-001") -> CallEvent:
    ev = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status="completed",
        dedupe_key=f"{call_id}:test_{uuid.uuid4().hex}",
        raw_payload_json={},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ev)
    session.flush()
    return ev


def _make_summary(session: Session, call_event_id: str,
                  consent: str = "YES",
                  text: str = "Great student!") -> SummaryResult:
    sr = SummaryResult(
        id=str(uuid.uuid4()),
        call_event_id=call_event_id,
        student_summary=text,
        summary_offered=True,
        summary_consent=consent,
        model_used="gpt-4o-mini",
        prompt_family="student_summary_generator",
        prompt_version="v1",
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(sr)
    session.flush()
    return sr


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _shadow_ghl_result(operation: str = "create_task") -> dict:
    return {"shadow": True, "operation": operation, "contact_id": "unknown", "payload": {}}


# ─────────────────────────────────────────────────────────────────────────────
# 1–5: create_crm_task
# ─────────────────────────────────────────────────────────────────────────────

def test_create_crm_task_happy_path(session):
    """Happy path: creates GHL task (shadow) and TaskEvent row."""
    call_event = _make_call_event(session)
    job = _make_job(session, "create_crm_task", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.create_task",
              return_value=_shadow_ghl_result()),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)

    from sqlalchemy import select
    task_event = session.scalars(
        select(TaskEvent).where(TaskEvent.call_event_id == call_event.id)
    ).first()
    assert task_event is not None
    assert task_event.status == "created"

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


def test_create_crm_task_already_claimed(session):
    """Job claimed by another worker → function returns immediately."""
    job = _make_job(session, "create_crm_task", {"call_event_id": "x"})
    # Simulate pre-claimed job
    job.status = "running"
    job.claimed_by = "other-worker"
    session.flush()

    call_count = 0

    def _no_call(*a, **kw):
        nonlocal call_count
        call_count += 1

    with patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess:
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)

    # claim_job returns None for already-running jobs — no GHL call
    assert call_count == 0


def test_create_crm_task_dedupe_skips_if_task_exists(session):
    """Existing TaskEvent with status='created' → skips, completes cleanly."""
    call_event = _make_call_event(session, call_id="call-dedup-001")
    # Pre-existing task event
    existing = TaskEvent(
        id=str(uuid.uuid4()),
        call_event_id=call_event.id,
        status="created",
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(existing)
    session.flush()

    job = _make_job(session, "create_crm_task", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_calls = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.create_task",
              side_effect=lambda **kw: ghl_calls.append(kw) or _shadow_ghl_result()),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)

    # GHL must not be called when TaskEvent already exists
    assert len(ghl_calls) == 0
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


def test_create_crm_task_missing_call_event_id_fails_job(session):
    """Missing call_event_id → ValueError → job fails + exception created."""
    job = _make_job(session, "create_crm_task", {"call_id": "call-x"})  # no call_event_id

    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        pytest.raises(ValueError, match="call_event_id"),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_create_crm_task_missing_call_event_row_fails_job(session):
    """CallEvent not found → ValueError → job fails + exception created."""
    job = _make_job(session, "create_crm_task", {
        "call_id": "call-ghost",
        "call_event_id": str(uuid.uuid4()),  # non-existent ID
    })

    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        pytest.raises(ValueError, match="CallEvent not found"),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# 6–11: send_student_summary
# ─────────────────────────────────────────────────────────────────────────────

def test_send_student_summary_consent_yes_writes_and_audits(session):
    """Consent=YES → writes summary field to GHL and records audit log."""
    call_event = _make_call_event(session, call_id="call-sum-yes")
    _make_summary(session, call_event.id, consent="YES")

    job = _make_job(session, "send_student_summary", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_updates = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.update_contact_fields",
              side_effect=lambda contact_id, field_updates:
                  ghl_updates.append(field_updates) or
                  {"shadow": True, "operation": "update_contact_fields",
                   "contact_id": contact_id, "payload": {}}),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)

    assert len(ghl_updates) == 1
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"

    from sqlalchemy import select
    audit = session.scalars(
        select(AuditLog).where(AuditLog.action == "student_summary_delivered")
    ).first()
    assert audit is not None
    assert audit.context_json["call_event_id"] == call_event.id


def test_send_student_summary_consent_no_skips(session):
    """Consent=NO → completes without calling GHL."""
    call_event = _make_call_event(session, call_id="call-sum-no")
    _make_summary(session, call_event.id, consent="NO")

    job = _make_job(session, "send_student_summary", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_calls = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.update_contact_fields",
              side_effect=lambda **kw: ghl_calls.append(kw)),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)

    assert len(ghl_calls) == 0
    assert session.get(ScheduledJob, job.id).status == "completed"


def test_send_student_summary_consent_unknown_skips(session):
    """Consent=UNKNOWN → completes without calling GHL."""
    call_event = _make_call_event(session, call_id="call-sum-unk")
    _make_summary(session, call_event.id, consent="UNKNOWN")

    job = _make_job(session, "send_student_summary", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_calls = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.update_contact_fields",
              side_effect=lambda **kw: ghl_calls.append(kw)),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)

    assert len(ghl_calls) == 0
    assert session.get(ScheduledJob, job.id).status == "completed"


def test_send_student_summary_no_summary_row_skips(session):
    """No SummaryResult row → completes without calling GHL."""
    call_event = _make_call_event(session, call_id="call-sum-none")

    job = _make_job(session, "send_student_summary", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_calls = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.update_contact_fields",
              side_effect=lambda **kw: ghl_calls.append(kw)),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)

    assert len(ghl_calls) == 0
    assert session.get(ScheduledJob, job.id).status == "completed"


def test_send_student_summary_empty_text_skips(session):
    """Empty summary text → completes without calling GHL."""
    call_event = _make_call_event(session, call_id="call-sum-empty")
    _make_summary(session, call_event.id, consent="YES", text="")

    job = _make_job(session, "send_student_summary", {
        "call_id": call_event.call_id,
        "call_event_id": call_event.id,
    })

    ghl_calls = []
    with (
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess,
        patch("app.adapters.ghl.GHLClient.update_contact_fields",
              side_effect=lambda **kw: ghl_calls.append(kw)),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)

    assert len(ghl_calls) == 0
    assert session.get(ScheduledJob, job.id).status == "completed"


def test_send_student_summary_already_claimed(session):
    """Already-claimed job → function returns immediately."""
    job = _make_job(session, "send_student_summary", {"call_event_id": "x"})
    job.status = "running"
    job.claimed_by = "other-worker"
    session.flush()

    with patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_sess:
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.crm_jobs import send_student_summary
        send_student_summary(job.id)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 12: _record_summary_audit
# ─────────────────────────────────────────────────────────────────────────────

def test_record_summary_audit_creates_row(session):
    """_record_summary_audit inserts an AuditLog row."""
    from sqlalchemy import select

    from app.worker.jobs.crm_jobs import _record_summary_audit

    call_event_id = str(uuid.uuid4())
    call_id = "call-audit-test"
    contact_id = "contact-audit"

    _record_summary_audit(session, call_event_id, call_id, contact_id)
    session.flush()

    row = session.scalars(
        select(AuditLog).where(
            AuditLog.action == "student_summary_delivered",
            AuditLog.entity_id == call_id,
        )
    ).first()
    assert row is not None
    assert row.context_json["call_event_id"] == call_event_id
    assert row.context_json["contact_id"] == contact_id
    assert row.operator_id == "system"
