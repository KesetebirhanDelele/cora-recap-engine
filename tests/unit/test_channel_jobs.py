"""
Unit tests for app.worker.jobs.channel_jobs (send_sms_job, send_email_job).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.scheduled_job import ScheduledJob


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(session, job_type: str, contact_id: str, status: str = "pending") -> ScheduledJob:
    now = datetime.now(tz=timezone.utc)
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="lead",
        entity_id=contact_id,
        run_at=now,
        status=status,
        payload_json={"contact_id": contact_id, "phone": "+15550001234"},
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def _ctx(session):
    """Return a context-manager mock that yields the given SQLite session."""
    mock = MagicMock()
    mock.__enter__ = lambda _: session
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# send_sms_job
# ---------------------------------------------------------------------------

def test_send_sms_job_completes(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    job = _make_job(session, "send_sms", contact_id)

    with patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)):
        from app.worker.jobs.channel_jobs import send_sms_job
        send_sms_job(job.id)

    session.refresh(job)
    assert job.status == "completed"


def test_send_sms_job_already_claimed_is_noop(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    job = _make_job(session, "send_sms", contact_id, status="claimed")

    with patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)):
        from app.worker.jobs.channel_jobs import send_sms_job
        send_sms_job(job.id)

    session.refresh(job)
    assert job.status == "claimed"


# ---------------------------------------------------------------------------
# send_email_job
# ---------------------------------------------------------------------------

def test_send_email_job_completes(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    job = _make_job(session, "send_email", contact_id)

    with patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)):
        from app.worker.jobs.channel_jobs import send_email_job
        send_email_job(job.id)

    session.refresh(job)
    assert job.status == "completed"


def test_send_email_job_already_claimed_is_noop(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    job = _make_job(session, "send_email", contact_id, status="claimed")

    with patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)):
        from app.worker.jobs.channel_jobs import send_email_job
        send_email_job(job.id)

    session.refresh(job)
    assert job.status == "claimed"
