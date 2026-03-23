"""
Unit tests for app.core.lifecycle (lead state machine).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from app.core.lifecycle import TERMINAL_STATES, transition_lead_state
from app.models.lead_state import LeadState


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

def _make_lead(session, *, status=None, version=0) -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=str(uuid.uuid4()),
        normalized_phone="+15550001234",
        ai_campaign_value=None,
        status=status,
        version=version,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


# ---------------------------------------------------------------------------
# Terminal state blocking
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_state_blocks_all_transitions(session, terminal):
    lead = _make_lead(session, status=terminal)
    result = transition_lead_state(session, lead, "opt_out")
    assert result is None
    session.refresh(lead)
    assert lead.status == terminal  # unchanged


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_terminal_state_blocks_not_interested(session, terminal):
    lead = _make_lead(session, status=terminal)
    result = transition_lead_state(session, lead, "not_interested")
    assert result is None


# ---------------------------------------------------------------------------
# opt_out → do_not_call
# ---------------------------------------------------------------------------

def test_opt_out_from_none(session):
    lead = _make_lead(session, status=None)
    result = transition_lead_state(session, lead, "opt_out")
    assert result == "do_not_call"
    session.refresh(lead)
    assert lead.status == "do_not_call"


def test_opt_out_from_active(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "opt_out")
    assert result == "do_not_call"


def test_opt_out_from_nurture(session):
    lead = _make_lead(session, status="nurture")
    result = transition_lead_state(session, lead, "opt_out")
    assert result == "do_not_call"


def test_opt_out_from_cold(session):
    lead = _make_lead(session, status="cold")
    result = transition_lead_state(session, lead, "opt_out")
    assert result == "do_not_call"


# ---------------------------------------------------------------------------
# wrong_number → invalid
# ---------------------------------------------------------------------------

def test_wrong_number_from_active(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "wrong_number")
    assert result == "invalid"
    session.refresh(lead)
    assert lead.status == "invalid"


def test_wrong_number_from_none(session):
    lead = _make_lead(session, status=None)
    result = transition_lead_state(session, lead, "wrong_number")
    assert result == "invalid"


# ---------------------------------------------------------------------------
# not_interested → closed
# ---------------------------------------------------------------------------

def test_not_interested_from_active(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "not_interested")
    assert result == "closed"
    session.refresh(lead)
    assert lead.status == "closed"


def test_not_interested_from_cold(session):
    lead = _make_lead(session, status="cold")
    result = transition_lead_state(session, lead, "not_interested")
    assert result == "closed"


# ---------------------------------------------------------------------------
# interested_not_now → nurture (only from None, active, cold)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("from_status", [None, "active", "cold"])
def test_interested_not_now_allowed_from(session, from_status):
    lead = _make_lead(session, status=from_status)
    result = transition_lead_state(session, lead, "interested_not_now")
    assert result == "nurture"
    session.refresh(lead)
    assert lead.status == "nurture"


def test_interested_not_now_blocked_from_nurture(session):
    lead = _make_lead(session, status="nurture")
    result = transition_lead_state(session, lead, "interested_not_now")
    assert result is None
    session.refresh(lead)
    assert lead.status == "nurture"  # unchanged


# ---------------------------------------------------------------------------
# uncertain → nurture (only from None, active, cold)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("from_status", [None, "active", "cold"])
def test_uncertain_allowed_from(session, from_status):
    lead = _make_lead(session, status=from_status)
    result = transition_lead_state(session, lead, "uncertain")
    assert result == "nurture"


def test_uncertain_blocked_from_nurture(session):
    lead = _make_lead(session, status="nurture")
    result = transition_lead_state(session, lead, "uncertain")
    assert result is None


# ---------------------------------------------------------------------------
# timeout → cold (only from nurture)
# ---------------------------------------------------------------------------

def test_timeout_from_nurture(session):
    lead = _make_lead(session, status="nurture")
    result = transition_lead_state(session, lead, "timeout")
    assert result == "cold"
    session.refresh(lead)
    assert lead.status == "cold"


def test_timeout_blocked_from_active(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "timeout")
    assert result is None


def test_timeout_blocked_from_none(session):
    lead = _make_lead(session, status=None)
    result = transition_lead_state(session, lead, "timeout")
    assert result is None


# ---------------------------------------------------------------------------
# Unknown event
# ---------------------------------------------------------------------------

def test_unknown_event_returns_none(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "nonexistent_event")
    assert result is None
    session.refresh(lead)
    assert lead.status == "active"  # unchanged


# ---------------------------------------------------------------------------
# Version increment and optimistic concurrency
# ---------------------------------------------------------------------------

def test_successful_transition_increments_version(session):
    lead = _make_lead(session, status="active", version=3)
    transition_lead_state(session, lead, "opt_out")
    session.refresh(lead)
    assert lead.version == 4


def test_version_conflict_returns_none(session):
    lead = _make_lead(session, status="active", version=0)
    # Simulate another worker winning by bumping version externally.
    # synchronize_session=False prevents SQLAlchemy from updating the
    # Python object, leaving lead.version=0 while the DB has version=99.
    from sqlalchemy import update
    session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id)
        .values(version=99)
        .execution_options(synchronize_session=False)
    )
    session.flush()
    # lead.version is still 0; DB has 99 — transition must detect the conflict
    result = transition_lead_state(session, lead, "opt_out")
    assert result is None


# ---------------------------------------------------------------------------
# cold is not terminal
# ---------------------------------------------------------------------------

def test_cold_is_not_terminal():
    assert "cold" not in TERMINAL_STATES


def test_cold_can_transition_to_nurture(session):
    lead = _make_lead(session, status="cold")
    result = transition_lead_state(session, lead, "interested_not_now")
    assert result == "nurture"


# ---------------------------------------------------------------------------
# reactivate — cold/nurture → active (new)
# ---------------------------------------------------------------------------

def test_reactivate_from_cold(session):
    lead = _make_lead(session, status="cold")
    result = transition_lead_state(session, lead, "reactivate")
    assert result == "active"
    session.refresh(lead)
    assert lead.status == "active"


def test_reactivate_from_nurture(session):
    lead = _make_lead(session, status="nurture")
    result = transition_lead_state(session, lead, "reactivate")
    assert result == "active"
    session.refresh(lead)
    assert lead.status == "active"


def test_reactivate_blocked_from_active(session):
    lead = _make_lead(session, status="active")
    result = transition_lead_state(session, lead, "reactivate")
    assert result is None
    session.refresh(lead)
    assert lead.status == "active"


def test_reactivate_blocked_from_terminal(session):
    for terminal in sorted(TERMINAL_STATES):
        lead = _make_lead(session, status=terminal)
        result = transition_lead_state(session, lead, "reactivate")
        assert result is None
