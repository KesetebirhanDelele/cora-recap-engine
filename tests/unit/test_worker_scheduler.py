"""
Phase 6 — worker scheduler tests.

DB interactions use SQLite in-memory.
RQ interactions are mocked.

Covers:
  1.  schedule_job: creates a ScheduledJob record in Postgres
  2.  schedule_job: default status is 'pending'
  3.  schedule_job: with rq_queue and is-due run_at — enqueues in RQ and sets rq_job_id
  4.  schedule_job: future run_at — creates DB record but does NOT enqueue
  5.  schedule_job: RQ failure — job stays pending in DB (resilient)
  6.  enqueue_now: enqueues a pending job and sets rq_job_id
  7.  enqueue_now: returns False for non-pending job
  8.  cancel_job (via scheduler re-export): pending → cancelled
  9.  get_job_registry: contains all expected job_type keys
  10. voicemail tier: _get_next_tier transitions correctly
  11. voicemail tier: _get_next_tier returns None at terminal tier
  12. voicemail tier: _get_next_tier returns None for unknown tier
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob

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


def _mock_rq_queue(job_id: str = "rq-job-001") -> MagicMock:
    q = MagicMock()
    rq_job = MagicMock()
    rq_job.id = job_id
    q.enqueue.return_value = rq_job
    return q


# ─────────────────────────────────────────────────────────────────────────────
# 1-2. schedule_job creates DB record
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_job_creates_record(session):
    from app.worker.scheduler import schedule_job

    job = schedule_job(
        session=session,
        job_type="run_call_analysis",
        entity_type="call",
        entity_id="call-123",
        run_at=_now(),
    )
    assert job.id is not None
    persisted = session.get(ScheduledJob, job.id)
    assert persisted is not None
    assert persisted.job_type == "run_call_analysis"


def test_schedule_job_default_status_pending(session):
    from app.worker.scheduler import schedule_job

    job = schedule_job(
        session=session, job_type="test", entity_type="call",
        entity_id="call-x", run_at=_now(),
    )
    assert job.status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# 3. schedule_job: immediate run enqueues in RQ
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_job_enqueues_immediately_when_due(session):
    from app.worker.scheduler import schedule_job

    mock_q = _mock_rq_queue("rq-immediate-01")
    mock_fn = MagicMock()
    past_time = _now() - timedelta(seconds=1)

    job = schedule_job(
        session=session, job_type="process_call_event",
        entity_type="call", entity_id="call-due",
        run_at=past_time,
        rq_queue=mock_q, rq_job_func=mock_fn,
    )

    mock_q.enqueue.assert_called_once_with(mock_fn, job.id)
    assert job.rq_job_id == "rq-immediate-01"


# ─────────────────────────────────────────────────────────────────────────────
# 4. schedule_job: future run — no immediate enqueue
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_job_future_does_not_enqueue(session):
    from app.worker.scheduler import schedule_job

    mock_q = _mock_rq_queue()
    future = _now() + timedelta(hours=2)

    job = schedule_job(
        session=session, job_type="process_voicemail_tier",
        entity_type="lead", entity_id="lead-future",
        run_at=future,
        rq_queue=mock_q, rq_job_func=MagicMock(),
    )

    mock_q.enqueue.assert_not_called()
    assert job.rq_job_id is None
    assert job.status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# 5. schedule_job: RQ failure — job stays pending
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_job_rq_failure_job_stays_pending(session):
    from app.worker.scheduler import schedule_job

    mock_q = MagicMock()
    mock_q.enqueue.side_effect = ConnectionError("Redis down")

    job = schedule_job(
        session=session, job_type="process_call_event",
        entity_type="call", entity_id="call-redis-fail",
        run_at=_now() - timedelta(seconds=1),
        rq_queue=mock_q, rq_job_func=MagicMock(),
    )

    assert job.status == "pending"
    assert job.rq_job_id is None


# ─────────────────────────────────────────────────────────────────────────────
# 6-7. enqueue_now
# ─────────────────────────────────────────────────────────────────────────────

def test_enqueue_now_returns_true_and_sets_rq_job_id(session):
    from app.worker.scheduler import enqueue_now

    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="test", entity_type="call",
        entity_id="e-1", run_at=_now(), status="pending",
        version=0, created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()

    mock_q = _mock_rq_queue("rq-enqueue-001")
    result = enqueue_now(session, job, mock_q, MagicMock())
    assert result is True
    assert job.rq_job_id == "rq-enqueue-001"


def test_enqueue_now_returns_false_for_non_pending(session):
    from app.worker.scheduler import enqueue_now

    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="test", entity_type="call",
        entity_id="e-2", run_at=_now(), status="completed",
        version=0, created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()

    mock_q = _mock_rq_queue()
    result = enqueue_now(session, job, mock_q, MagicMock())
    assert result is False
    mock_q.enqueue.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 8. cancel_job via scheduler re-export
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_cancel_job_pending(session):
    from app.worker.scheduler import cancel_job

    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="test", entity_type="call",
        entity_id="e-3", run_at=_now(), status="pending",
        version=0, created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()

    result = cancel_job(session, job.id)
    assert result is True
    session.refresh(job)
    assert job.status == "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# 9. get_job_registry
# ─────────────────────────────────────────────────────────────────────────────

def test_job_registry_contains_expected_types():
    from app.worker.main import get_job_registry

    registry = get_job_registry()
    assert "process_call_event" in registry
    assert "run_call_analysis" in registry
    assert "process_voicemail_tier" in registry


def test_job_registry_values_are_callable():
    from app.worker.main import get_job_registry

    registry = get_job_registry()
    for job_type, fn in registry.items():
        assert callable(fn), f"{job_type} must be callable"


# ─────────────────────────────────────────────────────────────────────────────
# 10-12. Voicemail tier logic
# ─────────────────────────────────────────────────────────────────────────────

def test_get_next_tier_none_to_0():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier(None) == "0"


def test_get_next_tier_0_to_1():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("0") == "1"


def test_get_next_tier_1_to_2():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("1") == "2"


def test_get_next_tier_2_to_3():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("2") == "3"


def test_get_next_tier_3_is_terminal():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("3") is None


def test_get_next_tier_unknown_returns_none():
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("99") is None
    assert _get_next_tier("invalid") is None
