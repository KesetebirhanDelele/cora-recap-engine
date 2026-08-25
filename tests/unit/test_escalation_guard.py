"""
Unit tests for app.core.escalation_guard.check_urgent_unresolved().
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.escalation_guard import check_urgent_unresolved
from app.models.base import Base
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState


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


def _make_call_event(
    session,
    contact_id: str,
    *,
    detected_intent: str | None,
    created_at: datetime,
) -> CallEvent:
    ce = CallEvent(
        id=str(uuid.uuid4()),
        call_id=str(uuid.uuid4()),
        contact_id=contact_id,
        dedupe_key=str(uuid.uuid4()),
        detected_intent=detected_intent,
        created_at=created_at,
        start_time_utc=created_at,
    )
    session.add(ce)
    session.flush()
    return ce


def _make_lead(session, contact_id: str, *, sales_outcome: str | None = None) -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        sales_outcome=sales_outcome,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


def test_no_call_events_returns_none(session):
    assert check_urgent_unresolved(session, "contact-1") is None


def test_no_contact_id_returns_none(session):
    assert check_urgent_unresolved(session, "") is None


def test_recent_human_transfer_request_blocks(session):
    contact_id = "contact-1"
    _make_call_event(
        session, contact_id,
        detected_intent="human_transfer_request",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )
    result = check_urgent_unresolved(session, contact_id)
    assert result is not None
    assert result["detected_intent"] == "human_transfer_request"


def test_recent_callback_with_time_blocks(session):
    contact_id = "contact-2"
    _make_call_event(
        session, contact_id,
        detected_intent="callback_with_time",
        created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=18),
    )
    result = check_urgent_unresolved(session, contact_id)
    assert result is not None
    assert result["detected_intent"] == "callback_with_time"


def test_non_urgent_intent_does_not_block(session):
    contact_id = "contact-3"
    _make_call_event(
        session, contact_id,
        detected_intent="re_engaged",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )
    assert check_urgent_unresolved(session, contact_id) is None


def test_escalation_older_than_30_days_does_not_block(session):
    contact_id = "contact-4"
    _make_call_event(
        session, contact_id,
        detected_intent="enrolled",
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=31),
    )
    assert check_urgent_unresolved(session, contact_id) is None


def test_escalation_within_30_days_boundary_blocks(session):
    contact_id = "contact-5"
    _make_call_event(
        session, contact_id,
        detected_intent="callback_request",
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=29),
    )
    assert check_urgent_unresolved(session, contact_id) is not None


def test_resolved_by_sales_rep_does_not_block(session):
    """Once a rep records a sales_outcome, triage is done — resume normal cadence."""
    contact_id = "contact-6"
    _make_call_event(
        session, contact_id,
        detected_intent="human_transfer_request",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )
    _make_lead(session, contact_id, sales_outcome="booked")
    assert check_urgent_unresolved(session, contact_id) is None


def test_most_recent_urgent_call_is_returned(session):
    contact_id = "contact-7"
    older = _make_call_event(
        session, contact_id,
        detected_intent="callback_request",
        created_at=datetime.now(tz=timezone.utc) - timedelta(days=5),
    )
    newer = _make_call_event(
        session, contact_id,
        detected_intent="human_transfer_request",
        created_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
    )
    result = check_urgent_unresolved(session, contact_id)
    assert result["call_event_id"] == newer.id
    assert result["call_event_id"] != older.id
