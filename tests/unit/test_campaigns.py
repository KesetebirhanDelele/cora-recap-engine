"""
Unit tests for app.core.campaigns.enter_campaign().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.core.campaigns import enter_campaign
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


@pytest.fixture(autouse=True)
def _no_student_lookup():
    """
    enter_campaign() calls student_guard.check_is_student(), which reaches GHL
    live. Default it to "not a student" for every test; the enrolled-student
    guard tests below override this with their own patch.
    """
    from unittest.mock import patch

    with patch("app.core.student_guard.check_is_student", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lead(
    session,
    *,
    phone: str = "+15550001234",
    status: str | None = "nurture",
    campaign_name: str | None = None,
    ai_campaign_value: str | None = "2",
    next_action_at: datetime | None = None,
) -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=str(uuid.uuid4()),
        normalized_phone=phone,
        status=status,
        campaign_name=campaign_name,
        ai_campaign_value=ai_campaign_value,
        next_action_at=next_action_at,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


def _make_job(
    session,
    contact_id: str,
    job_type: str = "launch_outbound_call",
    status: str = "pending",
) -> ScheduledJob:
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
    s.rq_default_queue = "default"
    s.redis_url = None
    s.redis_host = "localhost"
    s.redis_port = 6379
    s.redis_db = 0
    s.redis_username = None
    s.redis_password = None
    return s


# ---------------------------------------------------------------------------
# Unknown campaign_type
# ---------------------------------------------------------------------------

def test_enter_campaign_unknown_type_is_noop(session):
    lead = _make_lead(session)
    enter_campaign(session, lead, "nonexistent_campaign", settings=_mock_settings())
    # No exception, no jobs created
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Cold Lead campaign entry
# ---------------------------------------------------------------------------

def test_enter_campaign_sets_campaign_name(session):
    lead = _make_lead(session)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.campaign_name == "Cold Lead"


def test_enter_campaign_resets_ai_campaign_value(session):
    lead = _make_lead(session, ai_campaign_value="2")
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.ai_campaign_value is None


def test_enter_campaign_increments_version(session):
    lead = _make_lead(session)
    initial_version = lead.version
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.version == initial_version + 1


def test_enter_campaign_clears_stale_next_action_at(session):
    """
    Regression test for the 2026-08-24 dashboard visibility bug: a leftover
    next_action_at from a prior nurture wait (or any earlier state) must be
    cleared on campaign entry, or it silently outranks the fresh scheduled
    call in the dashboard's Scheduled Actions LEAST(job.run_at,
    next_action_at) computation and hides real upcoming calls from view.
    """
    stale = datetime(2026, 8, 19, 22, 11, 27, tzinfo=timezone.utc)
    lead = _make_lead(session, next_action_at=stale)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.next_action_at is None


def test_enter_campaign_schedules_outbound_call(session):
    lead = _make_lead(session)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["campaign_name"] == "Cold Lead"
    assert jobs[0].payload_json["phone_number"] == "+15550001234"


# ---------------------------------------------------------------------------
# Cancellation of existing jobs
# ---------------------------------------------------------------------------

def test_enter_campaign_cancels_pending_jobs(session):
    lead = _make_lead(session)
    j1 = _make_job(session, lead.contact_id, status="pending")
    j2 = _make_job(session, lead.contact_id, status="claimed")

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    session.refresh(j1)
    session.refresh(j2)
    assert j1.status == "cancelled"
    assert j2.status == "cancelled"


def test_enter_campaign_does_not_cancel_completed_jobs(session):
    lead = _make_lead(session)
    j1 = _make_job(session, lead.contact_id, status="completed")

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    session.refresh(j1)
    assert j1.status == "completed"


# ---------------------------------------------------------------------------
# No phone — outbound not scheduled
# ---------------------------------------------------------------------------

def test_enter_campaign_no_phone_skips_outbound(session):
    lead = _make_lead(session, phone="")
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_phone_shaped_contact_id_falls_back_to_contact_id(session):
    """
    Regression (spec/24): normalized_phone can be missing or corrupted (e.g.
    by the derived_phone bug fixed in ai_jobs.py/lifecycle_jobs.py, which
    once wrote Synthflow's own agent line into normalized_phone). When
    contact_id itself is phone-shaped (the phone-derived-contact_id
    convention documented in call_intake.py), enter_campaign must still
    schedule the outbound call using contact_id rather than skipping.
    """
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id="+15550009999",
        normalized_phone=None,
        status="nurture",
        campaign_name=None,
        ai_campaign_value="2",
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["phone_number"] == "+15550009999"


def test_enter_campaign_non_phone_contact_id_still_skips_without_normalized_phone(session):
    """
    Regression guard: when contact_id is a GHL-style ID (not phone-shaped)
    and normalized_phone is missing, there is no dialable number anywhere on
    the row — must still skip outbound, unchanged from pre-fix behavior.
    """
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=str(uuid.uuid4()),
        normalized_phone=None,
        status="nurture",
        campaign_name=None,
        ai_campaign_value="2",
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).all()
    assert len(jobs) == 0


# ---------------------------------------------------------------------------
# Idempotency — no duplicate outbound if pending already exists
# ---------------------------------------------------------------------------

def test_enter_campaign_idempotent_no_duplicate_outbound(session):
    lead = _make_lead(session)
    # First call
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    # Refresh to get updated version before second call
    session.refresh(lead)
    # Second call — should not create a second outbound job
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1


# ---------------------------------------------------------------------------
# Additional campaign types (new_lead)
# ---------------------------------------------------------------------------

def test_enter_campaign_new_lead_sets_campaign_name(session):
    lead = _make_lead(session)
    enter_campaign(session, lead, "new_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.campaign_name == "New Lead"


def test_enter_campaign_new_lead_schedules_outbound(session):
    lead = _make_lead(session)
    enter_campaign(session, lead, "new_lead", settings=_mock_settings())

    jobs = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["campaign_name"] == "New Lead"


# ---------------------------------------------------------------------------
# Outbound campaign pause — centralized check (regression guard for the
# intent_actions.py bypass: enter_campaign() must protect every caller
# uniformly, not just nurture_scheduler.py's own duplicate check).
# ---------------------------------------------------------------------------

def _mock_settings_paused():
    s = _mock_settings()
    s.outbound_campaigns_paused = True
    return s


def test_enter_campaign_skips_when_outbound_campaigns_paused(session):
    lead = _make_lead(session, campaign_name=None)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings_paused())
    session.refresh(lead)

    # No state change and no job scheduled — entry was skipped entirely.
    assert lead.campaign_name is None
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_new_lead_also_skipped_when_paused(session):
    lead = _make_lead(session, campaign_name=None)
    enter_campaign(session, lead, "new_lead", settings=_mock_settings_paused())
    session.refresh(lead)
    assert lead.campaign_name is None


def test_enter_campaign_proceeds_when_not_paused(session):
    """Sanity check: explicit outbound_campaigns_paused=False behaves like the default."""
    s = _mock_settings()
    s.outbound_campaigns_paused = False
    lead = _make_lead(session, campaign_name=None)
    enter_campaign(session, lead, "cold_lead", settings=s)
    session.refresh(lead)
    assert lead.campaign_name == "Cold Lead"


# ---------------------------------------------------------------------------
# Cold Lead-only campaign pause — narrower than outbound_campaigns_paused:
# holds Cold Lead entry only, New Lead must still proceed.
# ---------------------------------------------------------------------------

def _mock_settings_cold_lead_paused():
    s = _mock_settings()
    s.cold_lead_campaign_paused = True
    return s


def test_enter_campaign_skips_cold_lead_when_cold_lead_campaign_paused(session):
    lead = _make_lead(session, campaign_name=None)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings_cold_lead_paused())
    session.refresh(lead)

    assert lead.campaign_name is None
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_new_lead_unaffected_by_cold_lead_campaign_pause(session):
    """New Lead entry must proceed normally while only Cold Lead is paused."""
    lead = _make_lead(session, campaign_name=None)
    enter_campaign(session, lead, "new_lead", settings=_mock_settings_cold_lead_paused())
    session.refresh(lead)
    assert lead.campaign_name == "New Lead"


# ---------------------------------------------------------------------------
# Urgent-escalation guard — a lead that recently escalated to a human or had
# a callback/appointment booked must not be re-entered into a cold-pitch
# campaign until a sales rep has triaged it. Regression guard for the
# 2026-08-24 double-contact incident (Deborah: booked a callback at 2:54 PM,
# got a cold outbound pitch at 3:12 PM).
# ---------------------------------------------------------------------------

def _make_urgent_call_event(session, contact_id, detected_intent="human_transfer_request"):
    from app.models.call_event import CallEvent

    now = datetime.now(tz=timezone.utc)
    ce = CallEvent(
        id=str(uuid.uuid4()),
        call_id=str(uuid.uuid4()),
        contact_id=contact_id,
        dedupe_key=str(uuid.uuid4()),
        detected_intent=detected_intent,
        created_at=now,
        start_time_utc=now,
    )
    session.add(ce)
    session.flush()
    return ce


def test_enter_campaign_skips_when_urgent_escalation_unresolved(session):
    lead = _make_lead(session, campaign_name=None)
    _make_urgent_call_event(session, lead.contact_id)

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)

    assert lead.campaign_name is None
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_skips_when_do_not_call_set(session):
    """Regression guard for the previously-open lead_state.do_not_call gap (PROGRESS.md 2026-07-15)."""
    lead = _make_lead(session, campaign_name=None)
    lead.do_not_call = True
    session.flush()

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)

    assert lead.campaign_name is None
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_proceeds_when_do_not_call_false(session):
    lead = _make_lead(session, campaign_name=None)
    lead.do_not_call = False
    session.flush()

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)

    assert lead.campaign_name == "Cold Lead"


def test_enter_campaign_proceeds_when_escalation_already_resolved(session):
    lead = _make_lead(session, campaign_name=None)
    _make_urgent_call_event(session, lead.contact_id)
    lead.sales_outcome = "booked"
    session.flush()

    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)

    assert lead.campaign_name == "Cold Lead"


# ---------------------------------------------------------------------------
# Enrolled-student guard (spec/27) — GHL keeps enrolling current students
# into the cold-pitch campaign via a stale AI Campaign field; students must
# not be cold-called. Backstop for the GHL-side root cause.
# ---------------------------------------------------------------------------

def test_enter_campaign_skips_when_contact_is_student(session):
    from unittest.mock import patch

    lead = _make_lead(session, campaign_name=None)
    student = {
        "ghl_contact_id": "ghl-abc",
        "classification_source": "ghl_tags",
        "matched_tags": ["enrolled student"],
    }
    with patch("app.core.student_guard.check_is_student", return_value=student):
        enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)

    assert lead.campaign_name is None
    jobs = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead.contact_id)
    ).all()
    assert len(jobs) == 0


def test_enter_campaign_student_suppression_creates_no_exception(session):
    """A student suppression is expected GHL list churn — logged, not raised
    as a dashboard exception/alert (same as the do_not_call guard)."""
    from unittest.mock import patch

    from app.models.exception import ExceptionRecord

    lead = _make_lead(session, campaign_name=None)
    student = {
        "ghl_contact_id": "ghl-abc",
        "classification_source": "ghl_tags",
        "matched_tags": ["ipbc student"],
    }
    with patch("app.core.student_guard.check_is_student", return_value=student):
        enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    rows = session.scalars(
        select(ExceptionRecord).where(
            ExceptionRecord.type == "outbound_suppressed_student"
        )
    ).all()
    assert rows == []


def test_enter_campaign_proceeds_when_not_a_student(session):
    lead = _make_lead(session, campaign_name=None)
    # autouse fixture already returns None from check_is_student
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())
    session.refresh(lead)
    assert lead.campaign_name == "Cold Lead"


def test_enter_campaign_skips_student_check_when_settings_none(session):
    """settings is None → cannot build a GHLClient → student check is skipped
    (same graceful degradation as the pause-flag check), campaign proceeds."""
    from unittest.mock import patch

    lead = _make_lead(session, campaign_name=None)
    with patch("app.core.student_guard.check_is_student") as mock_check:
        enter_campaign(session, lead, "cold_lead", settings=None)
        mock_check.assert_not_called()
    session.refresh(lead)
    assert lead.campaign_name == "Cold Lead"


# ---------------------------------------------------------------------------
# Slot-aware run_at (spec/21) — regression guard for the "run_at=now, no
# grid" gap that let same-moment campaign entries collide with no spacing.
# ---------------------------------------------------------------------------

def test_enter_campaign_run_at_lands_on_shared_bucket_grid(session):
    from app.worker.jobs.outbound_jobs import _bucket_index, _bucket_start

    lead = _make_lead(session)
    enter_campaign(session, lead, "cold_lead", settings=_mock_settings())

    job = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == lead.contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
        )
    ).one()

    run_at = job.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    assert run_at == _bucket_start(_bucket_index(run_at))


def test_enter_campaign_two_leads_same_moment_land_in_different_buckets(session):
    lead_a = _make_lead(session)
    lead_b = _make_lead(session)

    enter_campaign(session, lead_a, "cold_lead", settings=_mock_settings())
    enter_campaign(session, lead_b, "cold_lead", settings=_mock_settings())

    job_a = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead_a.contact_id)
    ).one()
    job_b = session.scalars(
        select(ScheduledJob).where(ScheduledJob.entity_id == lead_b.contact_id)
    ).one()

    assert job_a.run_at != job_b.run_at
