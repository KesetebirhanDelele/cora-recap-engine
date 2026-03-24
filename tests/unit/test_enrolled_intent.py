"""
Tests for enrolled intent detection and campaign termination handler.

Covers:
  - detect_intent() returns 'enrolled' for enrollment phrases
  - Excluded vague booking phrases do NOT match enrolled
  - Priority: enrolled fires before re_engaged; not_interested shadows enrolled
  - _handle_enrolled() sets status='enrolled', ai_campaign_value='3'
  - _handle_enrolled() does not set next_action_at (nurture field)
  - evaluate_campaign_switch() returns None for enrolled (termination, not switch)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

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


def _make_lead(session, *, campaign_name="New Lead", status="active"):
    contact_id = str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone="+15550001234",
        status=status,
        campaign_name=campaign_name,
        ai_campaign_value=None,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


def _mock_settings():
    s = MagicMock()
    s.nurture_delay_days = 7
    s.sms_followup_delay_minutes = 30
    s.email_followup_delay_days = 1
    s.ghl_field_ai_campaign = "AI Campaign"
    return s


# ---------------------------------------------------------------------------
# Intent detection — enrolled phrases
# ---------------------------------------------------------------------------

class TestEnrolledIntentDetection:

    def _detect(self, phrase):
        from app.core.intent_detection import detect_intent
        return detect_intent(phrase)

    def test_id_like_to_enroll(self):
        r = self._detect("I'd like to enroll")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_i_want_to_enroll(self):
        r = self._detect("I want to enroll in the program")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_i_want_to_register(self):
        r = self._detect("I want to register")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_yes_ill_enroll(self):
        r = self._detect("yes I'll enroll")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_im_ready_to_enroll(self):
        r = self._detect("I'm ready to enroll")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_i_registered(self):
        r = self._detect("I registered already")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_ive_signed_up(self):
        r = self._detect("I've signed up")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_i_want_to_join_the_program(self):
        r = self._detect("I want to join the program")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_enrollment_confirmed(self):
        r = self._detect("enrollment confirmed")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_sign_me_up(self):
        r = self._detect("sign me up")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_i_completed_the_enrollment(self):
        r = self._detect("I completed the enrollment")
        assert r is not None
        assert r["intent"] == "enrolled"


# ---------------------------------------------------------------------------
# Excluded vague booking phrases — must NOT match enrolled
# ---------------------------------------------------------------------------

class TestExcludedBookingPhrases:

    def _detect(self, phrase):
        from app.core.intent_detection import detect_intent
        return detect_intent(phrase)

    def test_lets_book_it_not_enrolled(self):
        r = self._detect("let's book it")
        assert r is None or r["intent"] != "enrolled"

    def test_i_booked_not_enrolled(self):
        r = self._detect("I booked")
        assert r is None or r["intent"] != "enrolled"

    def test_booking_confirmed_not_enrolled(self):
        r = self._detect("booking confirmed")
        assert r is None or r["intent"] != "enrolled"


# ---------------------------------------------------------------------------
# Priority order
# ---------------------------------------------------------------------------

class TestEnrolledPriority:

    def _detect(self, phrase):
        from app.core.intent_detection import detect_intent
        return detect_intent(phrase)

    def test_enrolled_fires_before_re_engaged(self):
        """'sign me up' must route to enrolled, not re_engaged."""
        r = self._detect("sign me up, I'm interested")
        assert r is not None
        assert r["intent"] == "enrolled"

    def test_not_interested_shadows_enrolled(self):
        """Explicit rejection must override enrollment language."""
        r = self._detect("no thanks, not interested, don't sign me up")
        assert r is not None
        assert r["intent"] == "not_interested"

    def test_enrolled_fires_before_callback_with_time(self):
        """Enrollment + time reference must resolve to enrolled."""
        r = self._detect("yes I'll enroll, call me at 3pm to confirm")
        assert r is not None
        assert r["intent"] == "enrolled"


# ---------------------------------------------------------------------------
# _handle_enrolled — DB effects
# ---------------------------------------------------------------------------

class TestHandleEnrolled:

    def _run(self, session, lead):
        from app.core.intent_actions import _handle_enrolled
        with patch("app.adapters.ghl.GHLClient") as mock_ghl_cls:
            mock_ghl_cls.return_value.update_contact_fields.return_value = {}
            _handle_enrolled(
                session=session,
                contact_id=lead.contact_id,
                phone=lead.normalized_phone,
                entities={},
                settings=_mock_settings(),
            )
        return mock_ghl_cls

    def test_sets_status_enrolled(self, session):
        lead = _make_lead(session)
        self._run(session, lead)
        session.refresh(lead)
        assert lead.status == "enrolled"

    def test_sets_ai_campaign_value_3(self, session):
        lead = _make_lead(session)
        self._run(session, lead)
        session.refresh(lead)
        assert lead.ai_campaign_value == "3"

    def test_does_not_set_next_action_at(self, session):
        """Enrollment must not set next_action_at — that field is nurture only."""
        lead = _make_lead(session)
        self._run(session, lead)
        session.refresh(lead)
        assert lead.next_action_at is None

    def test_does_not_set_do_not_call(self, session):
        """Enrolled lead is a success — do_not_call must remain False."""
        lead = _make_lead(session)
        self._run(session, lead)
        session.refresh(lead)
        assert not lead.do_not_call

    def test_calls_ghl_ai_campaign_no(self, session):
        """GHL write must set AI Campaign = 'No' to stop campaign activity."""
        lead = _make_lead(session)
        mock_cls = self._run(session, lead)
        mock_cls.return_value.update_contact_fields.assert_called_once_with(
            contact_id=lead.contact_id,
            field_updates={"AI Campaign": "No"},
        )

    def test_ghl_failure_is_non_fatal(self, session):
        """GHL write failure must not raise — enrollment state is already saved."""
        from app.core.intent_actions import _handle_enrolled
        lead = _make_lead(session)
        with patch("app.adapters.ghl.GHLClient") as mock_ghl_cls:
            mock_ghl_cls.return_value.update_contact_fields.side_effect = RuntimeError("GHL down")
            # Should not raise
            _handle_enrolled(
                session=session,
                contact_id=lead.contact_id,
                phone=lead.normalized_phone,
                entities={},
                settings=_mock_settings(),
            )
        session.refresh(lead)
        assert lead.status == "enrolled"
        assert lead.ai_campaign_value == "3"


# ---------------------------------------------------------------------------
# No campaign switch for enrolled
# ---------------------------------------------------------------------------

class TestEnrolledNoCampaignSwitch:

    def test_evaluate_campaign_switch_returns_none_for_enrolled(self):
        from app.core.campaigns import evaluate_campaign_switch
        assert evaluate_campaign_switch("New Lead", "enrolled") is None
        assert evaluate_campaign_switch("Cold Lead", "enrolled") is None
