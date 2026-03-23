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
) -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=str(uuid.uuid4()),
        normalized_phone=phone,
        status=status,
        campaign_name=campaign_name,
        ai_campaign_value=ai_campaign_value,
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
