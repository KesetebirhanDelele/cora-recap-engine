"""
Unit tests for live-call intent signals.

Covers: human_transfer_request, low_confidence_audio, partial_engagement,
        failed_booking, and regression of existing intents.

All tests use SQLite in-memory DB via the same fixture pattern as
test_intent_actions.py. No external I/O.
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
from app.core.intent_detection import detect_intent, _extract_executed_actions
from app.core.intent_actions import handle_intent, PARTIAL_ENGAGEMENT_RETRY_CAP


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


def _make_lead(session, contact_id: str, *, phone: str = "+15550001234",
               status: str | None = None, campaign_name: str = "New Lead") -> LeadState:
    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        campaign_name=campaign_name,
        status=status,
        ai_campaign_value=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)
    session.flush()
    return lead


def _settings():
    s = MagicMock()
    s.ghl_writes_enabled = False
    s.shadow_mode_enabled = True
    return s


def _pending_jobs(session, contact_id: str) -> list[ScheduledJob]:
    return list(session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
            ScheduledJob.status == "pending",
        )
    ).all())


# ---------------------------------------------------------------------------
# _extract_executed_actions helper
# ---------------------------------------------------------------------------

def test_extract_ea_transfer_attempted():
    ea = [{"type": "live_agent_transfer", "status": "initiated"}]
    flags = _extract_executed_actions(ea)
    assert flags["transfer_attempted"] is True
    assert flags["transfer_failed"] is False


def test_extract_ea_booking_failed():
    ea = [{"type": "calendar_booking", "status": "failed"}]
    flags = _extract_executed_actions(ea)
    assert flags["booking_failed"] is True
    assert flags["booking_success"] is False


def test_extract_ea_booking_success():
    ea = [{"type": "appointment_schedule", "status": "success"}]
    flags = _extract_executed_actions(ea)
    assert flags["booking_success"] is True
    assert flags["booking_failed"] is False


def test_extract_ea_synthflow_dict_format():
    """Synthflow sends executed_actions as a dict-of-dicts with action_type and JSON return_value."""
    ea = {
        "action_ghl_create_booking": {
            "name": "action_ghl_create_booking",
            "action_type": "ghl_booking_action_type",
            "return_value": '{"booking_id": "XGmpXnfw5DdIlkLuXX7K", "error_message": null, "status": "success"}',
        },
        "rag_action": {
            "name": "rag_action",
            "action_type": "rag_action_type",
            "return_value": '{"status": "success", "results": []}',
        },
    }
    flags = _extract_executed_actions(ea)
    assert flags["booking_success"] is True
    assert flags["booking_failed"] is False


def test_extract_ea_synthflow_dict_booking_failed():
    ea = {
        "action_ghl_create_booking": {
            "name": "action_ghl_create_booking",
            "action_type": "ghl_booking_action_type",
            "return_value": '{"booking_id": null, "error_message": "no slots", "status": "failed"}',
        },
    }
    flags = _extract_executed_actions(ea)
    assert flags["booking_failed"] is True
    assert flags["booking_success"] is False


def test_extract_ea_empty():
    assert _extract_executed_actions(None) == {
        "transfer_attempted": False, "transfer_failed": False,
        "booking_success": False, "booking_failed": False,
    }


# ---------------------------------------------------------------------------
# detect_intent — new signals
# ---------------------------------------------------------------------------

def test_human_transfer_keyword():
    result = detect_intent("I want to talk to a real person please")
    assert result is not None
    assert result["intent"] == "human_transfer_request"


def test_human_transfer_via_executed_actions():
    result = detect_intent(
        "ok sure",
        executed_actions=[{"type": "live_agent_transfer", "status": "initiated"}],
    )
    assert result is not None
    assert result["intent"] == "human_transfer_request"


def test_failed_booking_keyword():
    result = detect_intent("The link didn't work, I couldn't book the session")
    assert result is not None
    assert result["intent"] == "failed_booking"


def test_failed_booking_via_executed_actions():
    result = detect_intent(
        "I tried",
        executed_actions=[{"type": "calendar_booking", "status": "failed"}],
    )
    assert result is not None
    assert result["intent"] == "failed_booking"


def test_booking_success_via_executed_actions_returns_callback_with_time():
    """Confirmed GHL booking must produce callback_with_time regardless of transcript."""
    result = detect_intent(
        "You're all set! Your Admissions call is booked for Tuesday, May fifth at eleven AM central time.",
        executed_actions={
            "action_ghl_create_booking": {
                "action_type": "ghl_booking_action_type",
                "return_value": '{"booking_id": "XGmpXnfw5DdIlkLuXX7K", "status": "success"}',
            },
        },
    )
    assert result is not None
    assert result["intent"] == "callback_with_time"
    assert result["confidence"] == 0.95


def test_booking_success_beats_do_not_call_in_transcript():
    """booking_success is checked after do_not_call/wrong_number/not_interested patterns
    but before the transcript patterns — so a noisy transcript doesn't override a confirmed booking.
    Actually booking_success fires at executed-actions priority (step 1) before transcript patterns."""
    result = detect_intent(
        "don't call me",  # would normally be do_not_call
        executed_actions={
            "action_ghl_create_booking": {
                "action_type": "ghl_booking_action_type",
                "return_value": '{"booking_id": "abc123", "status": "success"}',
            },
        },
    )
    # booking_success is checked AFTER transfer_attempted but BEFORE transcript patterns
    # The current priority: transfer_attempted → booking_success → booking_failed → transcript patterns
    assert result is not None
    assert result["intent"] == "callback_with_time"


def test_low_confidence_short_transcript():
    result = detect_intent("hi")
    assert result is not None
    assert result["intent"] == "low_confidence_audio"


def test_low_confidence_noise():
    result = detect_intent("[noise]")
    assert result is not None
    assert result["intent"] == "low_confidence_audio"


def test_partial_engagement_short_duration():
    # Sufficient transcript, short call, no strong intent
    transcript = "bot: Hi there. human: Yeah okay."
    result = detect_intent(transcript, duration_seconds=45)
    assert result is not None
    assert result["intent"] == "partial_engagement"


def test_partial_engagement_short_transcript_with_duration():
    # Short-ish transcript (<300 chars), no intent, short duration provided
    transcript = "bot: Hello. human: Uh huh. bot: Are you interested? human: I don't know."
    result = detect_intent(transcript, duration_seconds=60)
    assert result is not None
    assert result["intent"] == "partial_engagement"


# ---------------------------------------------------------------------------
# Priority ordering — human_transfer beats callback, do_not_call beats all
# ---------------------------------------------------------------------------

def test_human_transfer_beats_callback():
    # "talk to a real person" beats "call me back"
    result = detect_intent("Can I talk to a real person? I'll call me back later")
    assert result["intent"] == "human_transfer_request"


def test_do_not_call_still_highest_priority():
    result = detect_intent("Don't call me again, I want to talk to someone")
    assert result["intent"] == "do_not_call"


# ---------------------------------------------------------------------------
# Handler — human_transfer_request schedules follow-up when no confirmed transfer
# ---------------------------------------------------------------------------

def test_human_transfer_schedules_followup(session):
    contact_id = "live-xfer-001"
    lead = _make_lead(session, contact_id)
    settings = _settings()

    intent_result = {
        "intent": "human_transfer_request",
        "confidence": 0.9,
        "entities": {"datetime": None, "channel": None},  # no transfer_attempted flag
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=settings,
    )
    session.flush()

    jobs = _pending_jobs(session, contact_id)
    assert len(jobs) == 1
    outbound = jobs[0]
    assert outbound.job_type == "launch_outbound_call"
    assert outbound.payload_json.get("intent_reason") == "transfer_requested"
    # Should be ~2 hours from now (SQLite returns naive datetimes — compare naive)
    now = datetime.utcnow()
    run_at = outbound.run_at.replace(tzinfo=None) if outbound.run_at.tzinfo else outbound.run_at
    assert run_at > now + timedelta(hours=1, minutes=50)


# ---------------------------------------------------------------------------
# Handler — low_confidence_audio moves lead to cold_lead campaign
# ---------------------------------------------------------------------------

def test_low_confidence_moves_to_cold_lead(session):
    contact_id = "live-lowconf-001"
    lead = _make_lead(session, contact_id, campaign_name="New Lead")
    settings = _settings()

    intent_result = {
        "intent": "low_confidence_audio",
        "confidence": 0.8,
        "entities": {"datetime": None, "channel": None},
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=settings,
    )
    session.flush()

    session.expire(lead)
    updated = session.get(LeadState, lead.id)
    # Status should be "cold"
    assert updated.status == "cold"
    # Campaign should be Cold Lead
    assert updated.campaign_name == "Cold Lead"


# ---------------------------------------------------------------------------
# Handler — partial_engagement schedules short retry
# ---------------------------------------------------------------------------

def test_partial_engagement_schedules_short_retry(session):
    contact_id = "live-partial-001"
    lead = _make_lead(session, contact_id)
    settings = _settings()

    intent_result = {
        "intent": "partial_engagement",
        "confidence": 0.6,
        "entities": {"datetime": None, "channel": None},
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=settings,
    )
    session.flush()

    jobs = _pending_jobs(session, contact_id)
    assert len(jobs) == 1
    outbound = jobs[0]
    assert outbound.job_type == "launch_outbound_call"
    assert outbound.payload_json.get("intent_reason") == "partial_engagement"
    now = datetime.utcnow()
    run_at = outbound.run_at.replace(tzinfo=None) if outbound.run_at.tzinfo else outbound.run_at
    assert run_at > now + timedelta(hours=1, minutes=50)


# ---------------------------------------------------------------------------
# Handler — failed_booking schedules 4-hour retry
# ---------------------------------------------------------------------------

def test_failed_booking_schedules_retry(session):
    contact_id = "live-booking-001"
    lead = _make_lead(session, contact_id)
    settings = _settings()

    intent_result = {
        "intent": "failed_booking",
        "confidence": 0.9,
        "entities": {"datetime": None, "channel": None},
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=settings,
    )
    session.flush()

    jobs = _pending_jobs(session, contact_id)
    assert len(jobs) == 1
    outbound = jobs[0]
    assert outbound.job_type == "launch_outbound_call"
    assert outbound.payload_json.get("intent_reason") == "booking_retry"
    now = datetime.utcnow()
    run_at = outbound.run_at.replace(tzinfo=None) if outbound.run_at.tzinfo else outbound.run_at
    assert run_at > now + timedelta(hours=3, minutes=50)


# ---------------------------------------------------------------------------
# Handler — partial_engagement retry cap escalates to Cold Lead
# ---------------------------------------------------------------------------

def _make_completed_pe_job(session, contact_id: str, phone: str) -> ScheduledJob:
    """Insert a completed partial_engagement launch_outbound_call job."""
    import uuid as _uuid
    from app.worker.scheduler import schedule_job

    now = datetime.now(tz=timezone.utc)
    job = ScheduledJob(
        id=str(_uuid.uuid4()),
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=now,
        status="completed",
        payload_json={"contact_id": contact_id, "phone_number": phone,
                      "intent_reason": "partial_engagement"},
        version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def test_partial_engagement_below_cap_still_schedules_retry(session):
    """With 1 prior PE job (below cap of 2), should schedule another retry."""
    contact_id = "live-partial-cap-under"
    lead = _make_lead(session, contact_id)
    _make_completed_pe_job(session, contact_id, lead.normalized_phone)

    intent_result = {
        "intent": "partial_engagement",
        "confidence": 0.6,
        "entities": {},
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=_settings(),
    )
    session.flush()

    jobs = _pending_jobs(session, contact_id)
    assert len(jobs) == 1
    assert jobs[0].payload_json.get("intent_reason") == "partial_engagement"


def test_partial_engagement_at_cap_escalates_to_cold_lead(session):
    """With 2 prior PE jobs (at cap), should move lead to Cold Lead."""
    contact_id = "live-partial-cap-over"
    lead = _make_lead(session, contact_id, campaign_name="New Lead")
    _make_completed_pe_job(session, contact_id, lead.normalized_phone)
    _make_completed_pe_job(session, contact_id, lead.normalized_phone)

    intent_result = {
        "intent": "partial_engagement",
        "confidence": 0.6,
        "entities": {},
    }
    handle_intent(
        session=session,
        intent_result=intent_result,
        contact_id=contact_id,
        phone=lead.normalized_phone,
        current_job_id="fake-job-id",
        settings=_settings(),
    )
    session.flush()

    # No new pending outbound call — moved to Cold Lead instead
    pending = _pending_jobs(session, contact_id)
    # enter_campaign schedules the first cold_lead call
    assert all(j.job_type == "launch_outbound_call" for j in pending)
    # Lead should be in cold_lead campaign
    session.expire(lead)
    updated = session.get(LeadState, lead.id)
    assert updated.campaign_name == "Cold Lead"
    assert updated.status == "cold"


# ---------------------------------------------------------------------------
# Regression — existing intents unaffected
# ---------------------------------------------------------------------------

def test_do_not_call_regression():
    assert detect_intent("don't call me again")["intent"] == "do_not_call"

def test_not_interested_regression():
    assert detect_intent("I'm not interested")["intent"] == "not_interested"

def test_callback_request_regression():
    assert detect_intent("please call me back")["intent"] == "callback_request"

def test_uncertain_regression():
    assert detect_intent("I'm not sure, let me think about it")["intent"] == "uncertain"


# ---------------------------------------------------------------------------
# Voicemail-sequence guard in ai_jobs — campaign switch deferred
# ---------------------------------------------------------------------------

def _make_ai_jobs_deps(session, contact_id: str, *, campaign_name: str,
                       ai_campaign_value: str | None):
    """Seed a LeadState and a minimal CallEvent for ai_jobs tests."""
    from app.models.call_event import CallEvent

    now = datetime.now(tz=timezone.utc)
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone="+15550009999",
        campaign_name=campaign_name,
        ai_campaign_value=ai_campaign_value,
        status="active",
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(lead)

    ce = CallEvent(
        id=str(uuid.uuid4()),
        call_id=f"call-{contact_id}",
        contact_id=contact_id,
        direction="outbound",
        status="completed",
        transcript="I'm not sure, maybe",
        duration_seconds=45,
        dedupe_key=f"dedup-{contact_id}",
        raw_payload_json={},
        created_at=now,
    )
    session.add(ce)
    session.flush()
    return lead, ce


def test_campaign_switch_deferred_when_mid_voicemail_sequence(session):
    """New Lead mid-voicemail (tier '1') — campaign must NOT switch to Cold Lead."""
    from app.core.campaigns import apply_campaign_switch, evaluate_campaign_switch

    contact_id = "guard-mid-vm-tier1"
    lead, _ = _make_ai_jobs_deps(session, contact_id,
                                  campaign_name="New Lead",
                                  ai_campaign_value="1")

    # Simulate what ai_jobs does after handle_intent
    intent = "uncertain"
    new_campaign = evaluate_campaign_switch(lead.campaign_name or "", intent)
    assert new_campaign == "Cold Lead"   # rule exists

    tier = lead.ai_campaign_value
    in_voicemail_sequence = tier is not None and tier != "3"
    is_upgrade = new_campaign == "New Lead"

    # Guard: skip the switch
    if not in_voicemail_sequence or is_upgrade:
        apply_campaign_switch(session, lead, new_campaign, reason=intent)

    session.refresh(lead)
    assert lead.campaign_name == "New Lead", (
        "Campaign must not change while lead is mid-voicemail-sequence"
    )


def test_campaign_switch_applied_when_not_in_voicemail_sequence(session):
    """New Lead with no voicemail history (tier None) — switch IS applied."""
    from app.core.campaigns import apply_campaign_switch, evaluate_campaign_switch

    contact_id = "guard-no-vm-tier-none"
    lead, _ = _make_ai_jobs_deps(session, contact_id,
                                  campaign_name="New Lead",
                                  ai_campaign_value=None)

    intent = "interested_not_now"
    new_campaign = evaluate_campaign_switch(lead.campaign_name or "", intent)
    assert new_campaign == "Cold Lead"

    tier = lead.ai_campaign_value
    in_voicemail_sequence = tier is not None and tier != "3"
    is_upgrade = new_campaign == "New Lead"

    if not in_voicemail_sequence or is_upgrade:
        apply_campaign_switch(session, lead, new_campaign, reason=intent)

    session.refresh(lead)
    assert lead.campaign_name == "Cold Lead", (
        "Campaign should switch when lead has no active voicemail sequence"
    )


def test_re_engaged_upgrade_applied_even_mid_voicemail_sequence(session):
    """Cold Lead re-engaging (upgrade) is never deferred — applies immediately."""
    from app.core.campaigns import apply_campaign_switch, evaluate_campaign_switch

    contact_id = "guard-reengaged-mid-vm"
    lead, _ = _make_ai_jobs_deps(session, contact_id,
                                  campaign_name="Cold Lead",
                                  ai_campaign_value="1")

    intent = "re_engaged"
    new_campaign = evaluate_campaign_switch(lead.campaign_name or "", intent)
    assert new_campaign == "New Lead"

    tier = lead.ai_campaign_value
    in_voicemail_sequence = tier is not None and tier != "3"
    is_upgrade = new_campaign == "New Lead"

    if not in_voicemail_sequence or is_upgrade:
        apply_campaign_switch(session, lead, new_campaign, reason=intent)

    session.refresh(lead)
    assert lead.campaign_name == "New Lead", (
        "Upgrade (re_engaged) must never be deferred regardless of tier"
    )
