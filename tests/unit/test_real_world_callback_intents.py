"""
End-to-end tests for real-world callback intent phrases.

Covers the gap where relative time expressions ("in 20 minutes", "in 1 hour")
were not matched by callback_with_time patterns and fell through silently.

Each test asserts:
  - detect_intent() returns callback_with_time
  - entities["datetime"] is not None (no silent fallback)
  - datetime is correctly offset from now
  - handle_intent() schedules a launch_outbound_call job
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
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


def _now():
    return datetime.now(tz=timezone.utc)


def _make_lead(session, *, contact_id=None, phone="+15550001234"):
    contact_id = contact_id or str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        status="active",
        campaign_name="Cold Lead",
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
    return s


# ---------------------------------------------------------------------------
# detect_intent — relative time coverage
# ---------------------------------------------------------------------------

class TestRelativeTimeDetection:
    """Verify detect_intent returns callback_with_time for relative time phrases."""

    def _detect(self, phrase):
        from app.core.intent_detection import detect_intent
        return detect_intent(phrase)

    def test_in_20_minutes(self):
        result = self._detect("call me back in 20 minutes")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_in_1_minute(self):
        result = self._detect("call me back in 1 minute")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_in_1_hour(self):
        result = self._detect("call me in 1 hour")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_in_2_hours(self):
        result = self._detect("try me again in 2 hours")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_in_45_minutes(self):
        result = self._detect("I'm in a meeting, call me in 45 minutes")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_in_30_minutes_variant(self):
        result = self._detect("give me a ring in 30 minutes")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_existing_hours_still_works(self):
        """Regression: existing hour phrases must still match."""
        result = self._detect("call me back in 3 hours")
        assert result is not None
        assert result["intent"] == "callback_with_time"

    def test_existing_tomorrow_still_works(self):
        """Regression: absolute time phrases must still match."""
        result = self._detect("call me back tomorrow")
        assert result is not None
        assert result["intent"] == "callback_with_time"


# ---------------------------------------------------------------------------
# datetime extraction — minutes produce correct offset
# ---------------------------------------------------------------------------

class TestRelativeTimeDatetimeExtraction:
    """Verify entities['datetime'] is set and correctly offset for minute phrases."""

    def _extract(self, phrase):
        from app.core.intent_detection import detect_intent
        result = detect_intent(phrase)
        assert result is not None, f"No intent detected for: {phrase!r}"
        assert result["intent"] == "callback_with_time"
        return result["entities"]["datetime"]

    def test_20_minutes_datetime_not_none(self):
        dt = self._extract("call me back in 20 minutes")
        assert dt is not None, "datetime must not be None for 'in 20 minutes'"

    def test_20_minutes_offset_correct(self):
        before = _now()
        dt = self._extract("call me back in 20 minutes")
        after = _now()
        assert dt is not None
        expected_min = before + timedelta(minutes=19)
        expected_max = after + timedelta(minutes=21)
        assert expected_min <= dt <= expected_max, (
            f"Expected ~+20min from now, got {dt}"
        )

    def test_1_hour_offset_correct(self):
        before = _now()
        dt = self._extract("call me in 1 hour")
        after = _now()
        assert dt is not None
        assert before + timedelta(minutes=59) <= dt <= after + timedelta(minutes=61)

    def test_2_hours_offset_correct(self):
        before = _now()
        dt = self._extract("try me again in 2 hours")
        after = _now()
        assert dt is not None
        assert before + timedelta(hours=1, minutes=59) <= dt <= after + timedelta(hours=2, minutes=1)

    def test_45_minutes_offset_correct(self):
        before = _now()
        dt = self._extract("I'm in a meeting, call me in 45 minutes")
        after = _now()
        assert dt is not None
        assert before + timedelta(minutes=44) <= dt <= after + timedelta(minutes=46)

    def test_existing_hours_extraction_unchanged(self):
        """Regression: 'in 3 hours' must still extract correctly."""
        before = _now()
        dt = self._extract("call me back in 3 hours")
        after = _now()
        assert dt is not None
        assert before + timedelta(hours=2, minutes=59) <= dt <= after + timedelta(hours=3, minutes=1)


# ---------------------------------------------------------------------------
# No silent failure — callback_with_time always schedules a job
# ---------------------------------------------------------------------------

class TestCallbackInvariant:
    """
    Invariant: whenever callback_with_time is detected,
    a launch_outbound_call job MUST be scheduled.
    """

    def _run_handle_intent(self, session, transcript, contact_id, phone):
        from app.core.intent_actions import handle_intent
        from app.core.intent_detection import detect_intent

        result = detect_intent(transcript)
        assert result is not None, f"No intent for: {transcript!r}"
        assert result["intent"] == "callback_with_time"

        # Dummy current job to exclude from cancellation
        current_job = ScheduledJob(
            id=str(uuid.uuid4()),
            job_type="process_voicemail_tier",
            entity_type="lead",
            entity_id=contact_id,
            run_at=_now(),
            status="running",
            payload_json={"contact_id": contact_id},
            version=2,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(current_job)
        session.flush()

        handle_intent(
            session=session,
            intent_result=result,
            contact_id=contact_id,
            phone=phone,
            current_job_id=current_job.id,
            settings=_mock_settings(),
        )
        return result

    def _get_outbound_job(self, session, contact_id):
        return session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
                ScheduledJob.job_type == "launch_outbound_call",
                ScheduledJob.status == "pending",
            )
        ).first()

    def test_in_20_minutes_schedules_outbound_call(self, session):
        lead = _make_lead(session)
        self._run_handle_intent(
            session, "call me back in 20 minutes",
            lead.contact_id, lead.normalized_phone,
        )
        job = self._get_outbound_job(session, lead.contact_id)
        assert job is not None, "launch_outbound_call must be scheduled"
        assert job.status == "pending"

    def test_in_20_minutes_run_at_is_correct(self, session):
        lead = _make_lead(session)
        before = _now()
        self._run_handle_intent(
            session, "call me back in 20 minutes",
            lead.contact_id, lead.normalized_phone,
        )
        after = _now()
        job = self._get_outbound_job(session, lead.contact_id)
        assert job is not None
        # run_at must be ~+20min, not the fallback +2h
        # SQLite stores datetimes as naive; normalise before comparing
        run_at = job.run_at.replace(tzinfo=timezone.utc) if job.run_at.tzinfo is None else job.run_at
        assert run_at <= after + timedelta(minutes=21)
        assert run_at >= before + timedelta(minutes=19)

    def test_in_1_hour_schedules_outbound_call(self, session):
        lead = _make_lead(session)
        self._run_handle_intent(
            session, "call me in 1 hour",
            lead.contact_id, lead.normalized_phone,
        )
        job = self._get_outbound_job(session, lead.contact_id)
        assert job is not None

    def test_in_2_hours_schedules_outbound_call(self, session):
        lead = _make_lead(session)
        self._run_handle_intent(
            session, "I'm busy, try me again in 2 hours",
            lead.contact_id, lead.normalized_phone,
        )
        job = self._get_outbound_job(session, lead.contact_id)
        assert job is not None

    def test_fallback_used_when_no_time_extractable(self, session):
        """
        Negative guard: if callback_with_time fires but datetime is None,
        fallback (+2h) must still schedule a job — never silent.
        """
        from app.core.intent_actions import _handle_callback_with_time

        lead = _make_lead(session)
        before = _now()
        _handle_callback_with_time(
            session=session,
            contact_id=lead.contact_id,
            phone=lead.normalized_phone,
            entities={"datetime": None},   # force fallback path
            settings=_mock_settings(),
        )
        after = _now()
        job = self._get_outbound_job(session, lead.contact_id)
        assert job is not None, "Fallback must still schedule a job — no silent failure"
        # run_at should be ~+2h (CALLBACK_FALLBACK_MINUTES = 120)
        from app.core.intent_actions import CALLBACK_FALLBACK_MINUTES
        run_at = job.run_at.replace(tzinfo=timezone.utc) if job.run_at.tzinfo is None else job.run_at
        assert run_at >= before + timedelta(minutes=CALLBACK_FALLBACK_MINUTES - 1)
        assert run_at <= after + timedelta(minutes=CALLBACK_FALLBACK_MINUTES + 1)

    def test_callback_with_time_does_not_update_next_action_at(self, session):
        """
        callback_with_time must NOT set next_action_at on lead_state —
        that field is only for nurture. The action is a scheduled job, not a state update.
        """
        lead = _make_lead(session)
        self._run_handle_intent(
            session, "call me back in 20 minutes",
            lead.contact_id, lead.normalized_phone,
        )
        session.refresh(lead)
        assert lead.next_action_at is None, (
            "callback_with_time must not set next_action_at — that is for nurture only"
        )
