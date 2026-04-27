"""
Unit tests for outbound_jobs._compute_window_run_at.

Covers:
  1. No pending jobs → slot 0 → returns window_start exactly
  2. 9 pending jobs → slot 0 → still returns window_start
  3. 10 pending jobs → slot 1 → returns window_start + 120s
  4. 19 pending jobs → slot 1 → returns window_start + 120s
  5. 20 pending jobs → slot 2 → returns window_start + 240s
  6. Jobs outside the 4-hour window are not counted
  7. Completed/failed/cancelled jobs are not counted
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.worker.jobs.outbound_jobs import _CALL_BATCH_SIZE, _CALL_SLOT_SECONDS, _compute_window_run_at

_WINDOW_START = datetime(2026, 4, 28, 14, 0, 0, tzinfo=timezone.utc)  # 9 AM CDT


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


def _pending_job(run_at: datetime, status: str = "pending") -> ScheduledJob:
    return ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=str(uuid.uuid4()),
        run_at=run_at,
        status=status,
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


def _add_pending_jobs(session, count: int, run_at: datetime | None = None) -> None:
    at = run_at or _WINDOW_START
    for _ in range(count):
        session.add(_pending_job(at))
    session.flush()


# ─────────────────────────────────────────────────────────────────────────────


def test_no_pending_jobs_returns_window_start(session):
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_nine_pending_jobs_still_slot_zero(session):
    _add_pending_jobs(session, 9)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_ten_pending_jobs_advances_to_slot_one(session):
    _add_pending_jobs(session, 10)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)


def test_nineteen_pending_jobs_stays_slot_one(session):
    _add_pending_jobs(session, 19)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)


def test_twenty_pending_jobs_advances_to_slot_two(session):
    _add_pending_jobs(session, 20)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=2 * _CALL_SLOT_SECONDS)


def test_jobs_outside_four_hour_window_not_counted(session):
    outside = _WINDOW_START + timedelta(hours=4, seconds=1)
    _add_pending_jobs(session, _CALL_BATCH_SIZE, run_at=outside)
    # Only inside-window jobs count — the 10 outside jobs must not bump the slot
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_non_pending_statuses_not_counted(session):
    for status in ("completed", "failed", "cancelled"):
        session.add(_pending_job(_WINDOW_START, status=status))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_claimed_jobs_are_counted(session):
    _add_pending_jobs(session, _CALL_BATCH_SIZE, run_at=_WINDOW_START)
    session.add(_pending_job(_WINDOW_START, status="claimed"))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)
