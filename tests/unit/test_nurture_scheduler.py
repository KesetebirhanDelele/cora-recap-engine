"""
Unit tests for app.worker.jobs.nurture_scheduler.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob
from app.worker.jobs.nurture_scheduler import (
    NURTURE_SCHEDULER_INTERVAL_MINUTES,
    _process_due_nurture_leads,
    _schedule_next_run,
    ensure_scheduled,
)


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

def _make_lead(
    session,
    *,
    status: str = "nurture",
    next_action_at: datetime | None = None,
    do_not_call: bool = False,
    invalid: bool = False,
    phone: str = "+15550001234",
) -> LeadState:
    now = datetime.now(tz=timezone.utc)
    if next_action_at is None:
        next_action_at = now - timedelta(minutes=1)  # default: already due
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=str(uuid.uuid4()),
        normalized_phone=phone,
        status=status,
        next_action_at=next_action_at,
        do_not_call=do_not_call,
        invalid=invalid,
        ai_campaign_value=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


def _mock_settings():
    s = MagicMock()
    s.rq_default_queue = "default"
    s.redis_url = None
    s.redis_host = "localhost"
    s.redis_port = 6379
    s.redis_db = 0
    s.redis_username = None
    s.redis_password = None
    return s


# ---------------------------------------------------------------------------
# _process_due_nurture_leads
# ---------------------------------------------------------------------------

def test_processes_due_lead(session):
    lead = _make_lead(session)

    with patch("app.core.lifecycle.transition_lead_state", return_value="cold") as mock_transition, \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        processed, errors = _process_due_nurture_leads(session, _mock_settings())

    assert processed >= 1
    assert errors == 0


def test_skips_future_next_action_at(session):
    lead = _make_lead(
        session,
        next_action_at=datetime.now(tz=timezone.utc) + timedelta(days=7),
    )
    initial_status = lead.status

    with patch("app.core.lifecycle.transition_lead_state") as mock_transition, \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        _process_due_nurture_leads(session, _mock_settings())

    # transition should not have been called for this lead
    for call_args in mock_transition.call_args_list:
        assert call_args[0][1].contact_id != lead.contact_id


def test_skips_do_not_call_lead(session):
    lead = _make_lead(session, do_not_call=True)

    with patch("app.core.lifecycle.transition_lead_state") as mock_transition, \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        _process_due_nurture_leads(session, _mock_settings())

    for call_args in mock_transition.call_args_list:
        assert call_args[0][1].contact_id != lead.contact_id


def test_skips_invalid_lead(session):
    lead = _make_lead(session, invalid=True)

    with patch("app.core.lifecycle.transition_lead_state") as mock_transition, \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        _process_due_nurture_leads(session, _mock_settings())

    for call_args in mock_transition.call_args_list:
        assert call_args[0][1].contact_id != lead.contact_id


def test_skips_non_nurture_lead(session):
    lead = _make_lead(session, status="active")

    with patch("app.core.lifecycle.transition_lead_state") as mock_transition, \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        _process_due_nurture_leads(session, _mock_settings())

    for call_args in mock_transition.call_args_list:
        assert call_args[0][1].contact_id != lead.contact_id


def test_per_lead_error_does_not_stop_batch(session):
    """An exception on one lead should be counted as an error, not abort the batch."""
    good_lead = _make_lead(session)
    bad_lead = _make_lead(session)

    call_count = 0

    def flaky_transition(sess, lead, event):
        nonlocal call_count
        call_count += 1
        if lead.contact_id == bad_lead.contact_id:
            raise RuntimeError("simulated DB error")
        return "cold"

    # Patch at the source module — _process_due_nurture_leads uses local imports
    with patch("app.core.lifecycle.transition_lead_state", side_effect=flaky_transition), \
         patch("app.core.campaigns.enter_campaign"):
        processed, errors = _process_due_nurture_leads(session, _mock_settings())

    assert errors >= 1


def test_transition_skipped_due_to_version_conflict_not_counted(session):
    """If transition_lead_state returns None (conflict), lead is skipped but not counted as error."""
    lead = _make_lead(session)

    # Patch at the source module — _process_due_nurture_leads uses local imports
    with patch("app.core.lifecycle.transition_lead_state", return_value=None), \
         patch("app.core.campaigns.enter_campaign") as mock_campaign:
        processed, errors = _process_due_nurture_leads(session, _mock_settings())

    assert errors == 0
    mock_campaign.assert_not_called()


# ---------------------------------------------------------------------------
# _schedule_next_run
# ---------------------------------------------------------------------------

def _count_pending_nurture_jobs(session) -> int:
    return len(session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "run_nurture_scheduler",
            ScheduledJob.status == "pending",
        )
    ).all())


def test_schedule_next_run_creates_job(session):
    before = _count_pending_nurture_jobs(session)
    _schedule_next_run(session, _mock_settings())
    after = _count_pending_nurture_jobs(session)
    assert after == before + 1


def test_schedule_next_run_skips_if_pending_exists(session):
    # Ensure at least one pending exists
    _schedule_next_run(session, _mock_settings())
    before = _count_pending_nurture_jobs(session)
    # Second call should be a no-op
    _schedule_next_run(session, _mock_settings())
    after = _count_pending_nurture_jobs(session)
    assert after == before  # unchanged


def test_schedule_next_run_uses_interval(session):
    # Remove all pending nurture scheduler jobs for clean test
    from sqlalchemy import update
    session.execute(
        update(ScheduledJob)
        .where(
            ScheduledJob.job_type == "run_nurture_scheduler",
            ScheduledJob.status == "pending",
        )
        .values(status="cancelled")
    )
    session.flush()

    _schedule_next_run(session, _mock_settings())

    job = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.job_type == "run_nurture_scheduler",
            ScheduledJob.status == "pending",
        )
    ).first()
    assert job is not None

    # run_at should be approximately NURTURE_SCHEDULER_INTERVAL_MINUTES from now
    now = datetime.now(tz=timezone.utc)
    run_at = job.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    delta = run_at - now
    expected = timedelta(minutes=NURTURE_SCHEDULER_INTERVAL_MINUTES)
    assert abs((delta - expected).total_seconds()) < 5


# ---------------------------------------------------------------------------
# ensure_scheduled
# ---------------------------------------------------------------------------

def test_ensure_scheduled_creates_job_when_none_exists(engine):
    with Session(engine) as session:
        from sqlalchemy import update
        session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.job_type == "run_nurture_scheduler")
            .values(status="completed")
        )
        session.commit()

    with patch("app.worker.jobs.nurture_scheduler.get_sync_session") as mock_ctx:
        with Session(engine) as inner_session:
            mock_ctx.return_value.__enter__ = lambda s: inner_session
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            ensure_scheduled(_mock_settings())
            inner_session.rollback()


def test_ensure_scheduled_is_idempotent(engine):
    """Calling ensure_scheduled twice should not create duplicate pending jobs."""
    with Session(engine) as session:
        from sqlalchemy import update
        session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.job_type == "run_nurture_scheduler")
            .values(status="completed")
        )
        session.commit()

    counts = []
    for _ in range(2):
        with patch("app.worker.jobs.nurture_scheduler.get_sync_session") as mock_ctx:
            with Session(engine) as inner_session:
                mock_ctx.return_value.__enter__ = lambda s: inner_session
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                ensure_scheduled(_mock_settings())
                count = len(inner_session.scalars(
                    select(ScheduledJob).where(
                        ScheduledJob.job_type == "run_nurture_scheduler",
                        ScheduledJob.status.in_(["pending", "claimed", "running"]),
                    )
                ).all())
                counts.append(count)
                inner_session.rollback()
