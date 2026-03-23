"""
Unit tests for app.core.intent_actions.

Verifies correct lead_state mutations, job cancellations, and
outbound-call scheduling for each intent.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base

from app.core.intent_actions import (
    CALLBACK_FALLBACK_MINUTES,
    NURTURE_DELAY_DAYS,
    handle_intent,
    _cancel_contact_jobs,
    _update_lead_state,
)
from app.models.lead_state import LeadState
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

def _utc(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (UTC). SQLite returns naive datetimes."""
    if dt is None:
        return dt
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)



def _make_lead(session, contact_id: str, *, phone: str = "+15550001234") -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        ai_campaign_value=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


def _make_job(session, contact_id: str, job_type: str = "launch_outbound_call",
              status: str = "pending") -> ScheduledJob:
    now = datetime.now(tz=timezone.utc)
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="lead",
        entity_id=contact_id,
        run_at=now,
        status=status,
        payload_json={"contact_id": contact_id},
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def _mock_settings():
    s = MagicMock()
    s.nurture_delay_days = NURTURE_DELAY_DAYS
    s.rq_default_queue = "default"
    s.redis_url = None
    s.redis_host = "localhost"
    s.redis_port = 6379
    s.redis_db = 0
    s.redis_username = None
    s.redis_password = None
    return s


def _intent(intent: str, **entities) -> dict:
    return {
        "intent": intent,
        "confidence": 0.9,
        "entities": {"datetime": None, "channel": None, **entities},
    }


# ---------------------------------------------------------------------------
# _cancel_contact_jobs
# ---------------------------------------------------------------------------

def test_cancel_contact_jobs_cancels_pending(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    j1 = _make_job(session, contact_id, status="pending")
    j2 = _make_job(session, contact_id, status="pending")
    current_id = str(uuid.uuid4())  # fake current job

    cancelled = _cancel_contact_jobs(session, contact_id, exclude_job_id=current_id)

    assert cancelled == 2
    session.refresh(j1)
    session.refresh(j2)
    assert j1.status == "cancelled"
    assert j2.status == "cancelled"


def test_cancel_contact_jobs_excludes_current_job(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    j1 = _make_job(session, contact_id, status="pending")

    cancelled = _cancel_contact_jobs(session, contact_id, exclude_job_id=j1.id)

    assert cancelled == 0
    session.refresh(j1)
    assert j1.status == "pending"


def test_cancel_contact_jobs_ignores_completed(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    j1 = _make_job(session, contact_id, status="completed")
    j2 = _make_job(session, contact_id, status="failed")

    cancelled = _cancel_contact_jobs(session, contact_id, exclude_job_id="other")

    assert cancelled == 0
    session.refresh(j1)
    assert j1.status == "completed"


# ---------------------------------------------------------------------------
# _update_lead_state
# ---------------------------------------------------------------------------

def test_update_lead_state_sets_status(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    _update_lead_state(session, contact_id, status="nurture")

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.status == "nurture"
    assert lead.version == 1


def test_update_lead_state_sets_do_not_call(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    _update_lead_state(session, contact_id, do_not_call=True)

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.do_not_call is True


def test_update_lead_state_missing_row_is_noop(session):
    # Should log warning and not raise
    _update_lead_state(session, "nonexistent-contact", status="closed")


# ---------------------------------------------------------------------------
# handle_intent — do_not_call
# ---------------------------------------------------------------------------

def test_handle_intent_do_not_call_sets_flag(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    current_job_id = str(uuid.uuid4())

    handle_intent(
        session=session,
        intent_result=_intent("do_not_call"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=current_job_id,
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.do_not_call is True
    assert lead.status == "closed"


def test_handle_intent_do_not_call_cancels_pending_jobs(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    j1 = _make_job(session, contact_id, status="pending")
    j2 = _make_job(session, contact_id, status="claimed")
    current_job_id = str(uuid.uuid4())

    handle_intent(
        session=session,
        intent_result=_intent("do_not_call"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=current_job_id,
        settings=_mock_settings(),
    )

    session.refresh(j1)
    session.refresh(j2)
    assert j1.status == "cancelled"
    assert j2.status == "cancelled"


# ---------------------------------------------------------------------------
# handle_intent — wrong_number
# ---------------------------------------------------------------------------

def test_handle_intent_wrong_number_sets_invalid(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("wrong_number"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.invalid is True
    assert lead.status == "closed"


# ---------------------------------------------------------------------------
# handle_intent — not_interested
# ---------------------------------------------------------------------------

def test_handle_intent_not_interested_closes_lead(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("not_interested"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.status == "closed"


# ---------------------------------------------------------------------------
# handle_intent — interested_not_now (nurture)
# ---------------------------------------------------------------------------

def test_handle_intent_interested_not_now_sets_nurture_status(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("interested_not_now"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.status == "nurture"
    assert lead.next_action_at is not None
    # Should be ~7 days out
    delta = _utc(lead.next_action_at) - datetime.now(tz=timezone.utc)
    assert timedelta(days=6) < delta < timedelta(days=8)


def test_handle_intent_interested_not_now_does_not_schedule_outbound_call(session):
    # Nurture scheduler (not intent_actions) is responsible for scheduling the
    # cold campaign call once next_action_at has passed.
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("interested_not_now"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# handle_intent — callback_request
# ---------------------------------------------------------------------------

def test_handle_intent_callback_request_schedules_call_in_2h(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("callback_request"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 1
    delta = _utc(jobs[0].run_at) - datetime.now(tz=timezone.utc)
    expected = timedelta(minutes=CALLBACK_FALLBACK_MINUTES)
    assert abs(delta - expected) < timedelta(minutes=2)


# ---------------------------------------------------------------------------
# handle_intent — callback_with_time
# ---------------------------------------------------------------------------

def test_handle_intent_callback_with_time_uses_extracted_datetime(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)
    target = datetime.now(tz=timezone.utc) + timedelta(days=1)

    handle_intent(
        session=session,
        intent_result=_intent("callback_with_time", **{"datetime": target}),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 1
    # run_at should be close to the target (within 5 seconds of scheduling overhead)
    assert abs((_utc(jobs[0].run_at) - target).total_seconds()) < 5


def test_handle_intent_callback_with_time_falls_back_when_no_datetime(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("callback_with_time"),  # no datetime entity
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 1
    delta = _utc(jobs[0].run_at) - datetime.now(tz=timezone.utc)
    assert timedelta(minutes=CALLBACK_FALLBACK_MINUTES - 2) < delta


# ---------------------------------------------------------------------------
# handle_intent — channel switch
# ---------------------------------------------------------------------------

def test_handle_intent_request_sms_sets_preferred_channel(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("request_sms", channel="sms"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.preferred_channel == "sms"


def test_handle_intent_request_sms_schedules_send_sms_job(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("request_sms", channel="sms"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "send_sms",
        )
    ).all()
    assert len(jobs) == 1


def test_handle_intent_request_email_sets_preferred_channel(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("request_email", channel="email"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.preferred_channel == "email"


# ---------------------------------------------------------------------------
# handle_intent — uncertain
# ---------------------------------------------------------------------------

def test_handle_intent_uncertain_sets_nurture_status(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("uncertain"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead.status == "nurture"
    assert lead.next_action_at is not None


def test_handle_intent_uncertain_uses_shorter_delay_than_interested_not_now(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("uncertain"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    # uncertain delay = max(1, NURTURE_DELAY_DAYS // 2) = 3 days with default 7
    delta = _utc(lead.next_action_at) - datetime.now(tz=timezone.utc)
    assert timedelta(days=2) < delta < timedelta(days=4)


def test_handle_intent_uncertain_does_not_schedule_outbound_call(session):
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id)

    handle_intent(
        session=session,
        intent_result=_intent("uncertain"),
        contact_id=contact_id,
        phone="+15550001234",
        current_job_id=str(uuid.uuid4()),
        settings=_mock_settings(),
    )

    from sqlalchemy import select
    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# _ensure_utc
# ---------------------------------------------------------------------------

def test_ensure_utc_naive_datetime():
    from app.core.intent_actions import _ensure_utc
    naive = datetime(2025, 6, 1, 14, 0, 0)
    result = _ensure_utc(naive)
    assert result.tzinfo is not None
    assert result == datetime(2025, 6, 1, 14, 0, 0, tzinfo=timezone.utc)


def test_ensure_utc_already_utc():
    from app.core.intent_actions import _ensure_utc
    aware = datetime(2025, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    result = _ensure_utc(aware)
    assert result == aware


def test_ensure_utc_non_utc_timezone():
    import zoneinfo
    from app.core.intent_actions import _ensure_utc
    try:
        eastern = zoneinfo.ZoneInfo("America/New_York")
        aware_est = datetime(2025, 6, 1, 10, 0, 0, tzinfo=eastern)
        result = _ensure_utc(aware_est)
        assert result.tzinfo == timezone.utc
        assert result.hour == 14  # UTC = EST + 4h (summer)
    except (ImportError, KeyError):
        pytest.skip("zoneinfo not available or timezone not found")
