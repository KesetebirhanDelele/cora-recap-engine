"""
Unit tests for bidirectional campaign switching on intent detection.

Covers:
  - evaluate_campaign_switch() — pure rule lookup
  - apply_campaign_switch()    — DB write with optimistic concurrency
  - Integration via process_voicemail_tier intent path

Switch rules:
  New Lead + interested_not_now → Cold Lead
  New Lead + uncertain          → Cold Lead
  Cold Lead + re_engaged        → New Lead

No switch for: callback_with_time, callback_request, call_later_no_time,
not_interested, do_not_call, wrong_number, request_sms, request_email,
same-direction intents (cold lead + interested_not_now → stays cold).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.campaigns import apply_campaign_switch, evaluate_campaign_switch
from app.models.base import Base
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


def _now():
    return datetime.now(tz=timezone.utc)


def _make_lead(session, *, campaign_name: str, contact_id: str | None = None):
    contact_id = contact_id or str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone="+15550001234",
        status="active",
        campaign_name=campaign_name,
        ai_campaign_value=None,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


# ---------------------------------------------------------------------------
# evaluate_campaign_switch — pure function
# ---------------------------------------------------------------------------

class TestEvaluateCampaignSwitch:

    def test_new_lead_interested_not_now_returns_cold_lead(self):
        assert evaluate_campaign_switch("New Lead", "interested_not_now") == "Cold Lead"

    def test_new_lead_uncertain_returns_cold_lead(self):
        assert evaluate_campaign_switch("New Lead", "uncertain") == "Cold Lead"

    def test_cold_lead_re_engaged_returns_new_lead(self):
        assert evaluate_campaign_switch("Cold Lead", "re_engaged") == "New Lead"

    def test_cold_lead_interested_not_now_returns_none(self):
        """Cold lead saying 'not now' stays cold — no switch."""
        assert evaluate_campaign_switch("Cold Lead", "interested_not_now") is None

    def test_cold_lead_uncertain_returns_none(self):
        """Cold lead expressing uncertainty stays cold — no switch."""
        assert evaluate_campaign_switch("Cold Lead", "uncertain") is None

    def test_new_lead_re_engaged_returns_none(self):
        """New lead expressing interest stays new — already in fast cadence."""
        assert evaluate_campaign_switch("New Lead", "re_engaged") is None

    def test_callback_with_time_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "callback_with_time") is None
        assert evaluate_campaign_switch("Cold Lead", "callback_with_time") is None

    def test_callback_request_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "callback_request") is None
        assert evaluate_campaign_switch("Cold Lead", "callback_request") is None

    def test_call_later_no_time_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "call_later_no_time") is None
        assert evaluate_campaign_switch("Cold Lead", "call_later_no_time") is None

    def test_not_interested_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "not_interested") is None
        assert evaluate_campaign_switch("Cold Lead", "not_interested") is None

    def test_do_not_call_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "do_not_call") is None
        assert evaluate_campaign_switch("Cold Lead", "do_not_call") is None

    def test_wrong_number_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "wrong_number") is None

    def test_request_sms_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "request_sms") is None
        assert evaluate_campaign_switch("Cold Lead", "request_sms") is None

    def test_request_email_no_switch(self):
        assert evaluate_campaign_switch("New Lead", "request_email") is None
        assert evaluate_campaign_switch("Cold Lead", "request_email") is None

    def test_case_insensitive_campaign_name(self):
        assert evaluate_campaign_switch("new lead", "interested_not_now") == "Cold Lead"
        assert evaluate_campaign_switch("NEW LEAD", "uncertain") == "Cold Lead"
        assert evaluate_campaign_switch("cold lead", "re_engaged") == "New Lead"

    def test_none_campaign_name_returns_none(self):
        assert evaluate_campaign_switch(None, "interested_not_now") is None

    def test_unknown_intent_returns_none(self):
        assert evaluate_campaign_switch("New Lead", "unknown_intent") is None


# ---------------------------------------------------------------------------
# apply_campaign_switch — DB write
# ---------------------------------------------------------------------------

class TestApplyCampaignSwitch:

    def test_updates_campaign_name(self, session):
        lead = _make_lead(session, campaign_name="New Lead")
        apply_campaign_switch(session, lead, "Cold Lead", reason="interested_not_now")
        session.refresh(lead)
        assert lead.campaign_name == "Cold Lead"

    def test_increments_version(self, session):
        lead = _make_lead(session, campaign_name="New Lead")
        original_version = lead.version
        apply_campaign_switch(session, lead, "Cold Lead", reason="uncertain")
        session.refresh(lead)
        assert lead.version == original_version + 1

    def test_updates_updated_at(self, session):
        before = _now()
        lead = _make_lead(session, campaign_name="Cold Lead")
        apply_campaign_switch(session, lead, "New Lead", reason="re_engaged")
        session.refresh(lead)
        run_at = lead.updated_at.replace(tzinfo=timezone.utc) if lead.updated_at.tzinfo is None else lead.updated_at
        assert run_at >= before

    def test_version_conflict_does_not_raise(self, session):
        """Version conflict logs a warning and returns cleanly — no exception."""
        lead = _make_lead(session, campaign_name="New Lead")
        # Manually advance version to simulate concurrent update
        lead.version = 999
        # Should not raise
        apply_campaign_switch(session, lead, "Cold Lead", reason="uncertain")

    def test_cold_to_new_lead_switch(self, session):
        lead = _make_lead(session, campaign_name="Cold Lead")
        apply_campaign_switch(session, lead, "New Lead", reason="re_engaged")
        session.refresh(lead)
        assert lead.campaign_name == "New Lead"

    def test_does_not_reset_tier(self, session):
        """Switching campaign must not touch ai_campaign_value."""
        lead = _make_lead(session, campaign_name="New Lead")
        lead.ai_campaign_value = "1"
        session.flush()
        apply_campaign_switch(session, lead, "Cold Lead", reason="interested_not_now")
        session.refresh(lead)
        assert lead.ai_campaign_value == "1"

    def test_does_not_change_status(self, session):
        """Switching campaign must not touch lead status."""
        lead = _make_lead(session, campaign_name="New Lead")
        lead.status = "nurture"
        session.flush()
        apply_campaign_switch(session, lead, "Cold Lead", reason="uncertain")
        session.refresh(lead)
        assert lead.status == "nurture"


# ---------------------------------------------------------------------------
# re_engaged intent detection
# ---------------------------------------------------------------------------

class TestReEngagedIntentDetection:

    def _detect(self, phrase):
        from app.core.intent_detection import detect_intent
        return detect_intent(phrase)

    def test_actually_interested(self):
        r = self._detect("actually I'm interested")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_tell_me_more(self):
        r = self._detect("tell me more about it")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_id_like_to_hear_more(self):
        r = self._detect("I'd like to hear more")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_how_does_it_work(self):
        r = self._detect("how does it work?")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_sign_me_up(self):
        """sign me up is an enrollment signal — routes to enrolled, not re_engaged."""
        r = self._detect("go ahead and sign me up")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_ive_been_thinking_about_it(self):
        r = self._detect("I've been thinking about it and I want to know more")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_im_ready_to_talk(self):
        r = self._detect("I'm ready to talk now")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_re_engaged_before_callback_with_time(self):
        """re_engaged must fire before callback_with_time in priority order."""
        r = self._detect("actually I'm interested, call me at 3pm")
        assert r is not None
        assert r["intent"] == "re_engaged"

    def test_re_engaged_after_not_interested(self):
        """not_interested must shadow re_engaged when both match."""
        r = self._detect("no thanks, not interested in hearing more")
        assert r is not None
        assert r["intent"] == "not_interested"
