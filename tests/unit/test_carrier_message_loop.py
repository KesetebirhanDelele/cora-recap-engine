"""Carrier-message redial loop (spec/41): a carrier recording is not a lead asking to be called later.

Real transcripts from production (2026-10-19 .. 10-24): one number was dialled 15 times in 3 days, about every 2 hours, because
"We could not complete your call. Please try again." matched the call-later pattern, and call-later callbacks had no daily cap and no
retry count."""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import call_policy
from app.core import intent_actions as ia
from app.core.intent_detection import detect_intent, is_carrier_message
from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState

NOW = datetime.now(tz=timezone.utc)
COULD_NOT = "bot: Hi, this is Cora with Colabe\nhuman: We could not complete your call. Please try again."
NOT_IN_SERVICE = "bot: Hi, this is Cora\nhuman: Sorry. But this number is currently not in service. Please try again later."
REAL_LATER = "bot: Hi, this is Cora with Colaberry\nhuman: Yeah I'm driving right now, please call me later."


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def add_call(session, contact_id, transcript, *, days_ago=0.0, intent=None, direction="outbound"):
    t = NOW - timedelta(days=days_ago)
    session.add(CallEvent(id=str(uuid.uuid4()), call_id=str(uuid.uuid4()), contact_id=contact_id, direction=direction, status="completed",
                          dedupe_key=uuid.uuid4().hex, raw_payload_json={}, transcript=transcript, detected_intent=intent,
                          call_started_at=t, created_at=t, start_time_utc=t))
    session.flush()


def add_lead(session, contact_id):
    lead = LeadState(id=str(uuid.uuid4()), contact_id=contact_id, normalized_phone=contact_id, status="active", version=0,
                     created_at=NOW, updated_at=NOW, campaign_name="New Lead")
    session.add(lead)
    session.flush()
    return lead


def add_redial(session, contact_id, *, reason="call_later_no_time", status="pending", days_ago=0.0):
    session.add(ScheduledJob(id=str(uuid.uuid4()), job_type="launch_outbound_call", entity_type="lead", entity_id=contact_id, status=status,
                             run_at=NOW + timedelta(hours=2), payload_json={"contact_id": contact_id, "intent_reason": reason},
                             created_at=NOW - timedelta(days=days_ago), version=0))
    session.flush()


def redials(session, contact_id, status=None):
    rows = session.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "launch_outbound_call",
                                                      ScheduledJob.payload_json["contact_id"].as_string() == contact_id)).all()
    return [r for r in rows if status is None or r.status == status]


# -- detection ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("t,want", [
    (COULD_NOT, True), (NOT_IN_SERVICE, True),
    ("bot: hi\nhuman: The number you have dialed is not in service.", True),
    ("bot: hi\nhuman: This call cannot be completed as dialed.", True),
    (REAL_LATER, False),
    ("bot: hi\nhuman: yes this is Sam, go ahead", False),
    ("bot: hi\nhuman: my old number is not in service but I can talk now. " + "I run a company and we do a lot of data work. " * 6, False),   # a real conversation
    ("bot: could not complete your call", False),                          # the BOT's words never count
    ("", False), (None, False),
])
def test_is_carrier_message(t, want):
    assert is_carrier_message(t) is want


def test_a_carrier_recording_is_its_own_intent_not_call_later():
    for t in (COULD_NOT, NOT_IN_SERVICE):
        assert detect_intent(t, duration_seconds=12)["intent"] == "carrier_message"


def test_a_real_person_asking_to_be_called_later_is_still_a_callback_request():
    assert detect_intent(REAL_LATER, duration_seconds=40)["intent"] in ("callback_request", "call_later_no_time")   # existing rules, unchanged
    assert detect_intent("please try again")["intent"] == "call_later_no_time"          # existing behaviour for plain text


# -- the carrier-message handler -----------------------------------------------------------------------------------
def test_first_carrier_call_schedules_nothing_cancels_a_waiting_redial_and_keeps_the_lead_active(session):
    lead = add_lead(session, "+15550001")
    add_call(session, "+15550001", COULD_NOT)
    add_redial(session, "+15550001")
    ia._handle_carrier_message(session, "+15550001", "+15550001", {}, None)
    assert [r.status for r in redials(session, "+15550001")] == ["cancelled"]
    session.refresh(lead)
    assert not lead.invalid and lead.status == "active"


def test_second_carrier_call_in_the_window_marks_the_number_invalid(session):
    lead = add_lead(session, "+15550002")
    add_call(session, "+15550002", NOT_IN_SERVICE, days_ago=2)
    add_call(session, "+15550002", COULD_NOT)
    with patch.object(ia, "_write_ghl_campaign_off") as off:
        ia._handle_carrier_message(session, "+15550002", "+15550002", {}, None)
    session.refresh(lead)
    assert lead.invalid is True and lead.status == "closed" and off.called


def test_old_carrier_calls_outside_the_window_do_not_count(session):
    lead = add_lead(session, "+15550003")
    add_call(session, "+15550003", NOT_IN_SERVICE, days_ago=45)
    add_call(session, "+15550003", COULD_NOT)
    ia._handle_carrier_message(session, "+15550003", "+15550003", {}, None)
    session.refresh(lead)
    assert not lead.invalid


def test_the_carrier_handler_is_registered():
    assert ia._HANDLERS["carrier_message"] is ia._handle_carrier_message


# -- call-later callbacks now have a retry count --------------------------------------------------------------------
def test_call_later_schedules_up_to_the_cap_then_goes_cold(session):
    lead = add_lead(session, "+15550004")
    for _ in range(ia.CALL_LATER_RETRY_CAP):
        ia._handle_call_later_no_time(session, "+15550004", "+15550004", {}, None)
        for j in redials(session, "+15550004", "pending"):                  # the previous callback ran
            j.status = "completed"
        session.flush()
    assert len(redials(session, "+15550004")) == ia.CALL_LATER_RETRY_CAP
    ia._handle_call_later_no_time(session, "+15550004", "+15550004", {}, None)
    assert len(redials(session, "+15550004")) == ia.CALL_LATER_RETRY_CAP     # nothing new
    session.refresh(lead)
    assert lead.status == "cold"


def test_old_callbacks_outside_the_window_do_not_use_up_the_cap(session):
    add_lead(session, "+15550005")
    for _ in range(5):
        add_redial(session, "+15550005", status="completed", days_ago=ia.CALL_LATER_WINDOW_DAYS + 3)
    ia._handle_call_later_no_time(session, "+15550005", "+15550005", {}, None)
    assert len(redials(session, "+15550005", "pending")) == 1


# -- the daily cap no longer exempts call-later ---------------------------------------------------------------------
def test_call_later_is_no_longer_exempt_from_the_daily_cap_but_explicit_callbacks_are():
    assert call_policy.is_lead_requested_callback({"intent_reason": "call_later_no_time"}) is False
    for reason in ("callback_request", "callback_with_time", "transfer_requested"):
        assert call_policy.is_lead_requested_callback({"intent_reason": reason}) is True


# -- the cleanup script ----------------------------------------------------------------------------------------------
_SCRIPT = Path(__file__).resolve().parents[2] / "execution" / "fix_carrier_message_calls.py"
_spec = importlib.util.spec_from_file_location("fix_carrier", _SCRIPT)
FIX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FIX)


def seed_loop(session):
    add_lead(session, "+15551111")
    add_lead(session, "+15552222")
    for _ in range(3):                                                     # 3 carrier calls mislabelled call_later_no_time
        add_call(session, "+15551111", COULD_NOT, intent="call_later_no_time")
    add_call(session, "+15552222", NOT_IN_SERVICE, intent="call_later_no_time")     # a single one
    add_call(session, "+15552222", REAL_LATER, intent="call_later_no_time")         # a real person: must stay untouched
    add_redial(session, "+15551111")


def test_cleanup_dry_run_reports_and_changes_nothing(session):
    seed_loop(session)
    lines = []
    res = FIX.run(session, None, 60, apply=False, out=lines.append)
    assert res["calls"] == 4 and res["contacts"] == 2 and res["relabelled"] == 0
    assert "DRY RUN" in lines[-1] and not any("+1555" in ln for ln in lines)       # counts only, no phone numbers
    assert session.scalars(select(CallEvent).where(CallEvent.detected_intent == "carrier_message")).all() == []


def test_cleanup_apply_relabels_cancels_and_marks_only_the_repeat_offender_invalid(session):
    seed_loop(session)
    with patch.object(ia, "_write_ghl_campaign_off"):
        res = FIX.run(session, None, 60, apply=True, out=lambda *_: None)
    assert res["relabelled"] == 4 and res["redials_cancelled"] == 1 and res["marked_invalid"] == 1
    assert [r.status for r in redials(session, "+15551111")] == ["cancelled"]
    leads = {x.contact_id: x for x in session.scalars(select(LeadState)).all()}
    assert leads["+15551111"].invalid is True and not leads["+15552222"].invalid
    real = session.scalars(select(CallEvent).where(CallEvent.transcript == REAL_LATER)).one()
    assert real.detected_intent == "call_later_no_time"                                 # the real callback request is untouched


def test_cleanup_judges_invalid_on_the_30_day_rule_but_relabels_the_full_window(session):
    add_lead(session, "+15553333")
    add_call(session, "+15553333", NOT_IN_SERVICE, days_ago=45, intent="call_later_no_time")      # outside the 30-day strike window
    add_call(session, "+15553333", COULD_NOT, days_ago=1, intent="call_later_no_time")            # inside: only ONE strike
    with patch.object(ia, "_write_ghl_campaign_off"):
        res = FIX.run(session, None, 60, apply=True, out=lambda *_: None)
    assert res["relabelled"] == 2 and res["marked_invalid"] == 0                                    # both relabelled, number NOT invalidated
    assert not session.scalars(select(LeadState).where(LeadState.contact_id == "+15553333")).one().invalid
