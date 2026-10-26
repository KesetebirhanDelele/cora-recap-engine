"""spec/42: a booking is its own intent, callback detection reads only what the person said, and scheduled callbacks keep the lead's campaign.

Found on 2026-10-26 in 25 cancelled lead-requested callback jobs: 17 were successful bookings reported as callback_with_time, 2 were voicemail
greetings read as callback_request, and 2 matched only the bot's own words."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import intent_actions as ia
from app.core.intent_detection import detect_intent, human_text, is_voicemail_greeting
from app.models import Base, ScheduledJob
from app.models.lead_state import LeadState

NOW = datetime.now(tz=timezone.utc)
BOOKING = {"action_ghl_create_booking": {"name": "action_ghl_create_booking", "action_type": "ghl_booking_action_type",
                                          "return_value": json.dumps({"booking_id": "abc", "status": "success"})}}


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def add_lead(session, contact_id, campaign="Cold Lead"):
    session.add(LeadState(id=str(uuid.uuid4()), contact_id=contact_id, normalized_phone=contact_id, status="active", version=0,
                          created_at=NOW, updated_at=NOW, campaign_name=campaign))
    session.flush()


def jobs(session, contact_id):
    return session.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "launch_outbound_call",
                                                      ScheduledJob.payload_json["contact_id"].as_string() == contact_id)).all()


# -- a booking is its own intent --------------------------------------------------------------------------------------
def test_a_successful_booking_is_the_booked_intent_with_no_datetime():
    r = detect_intent("bot: You're all set for Tuesday at eleven.\nhuman: great, thanks", executed_actions=BOOKING)
    assert r["intent"] == "booked" and r["entities"]["booked"] is True and r["entities"]["datetime"] is None


def test_a_booking_never_schedules_a_redial(session):
    add_lead(session, "+15550001")
    ia.handle_intent(session, detect_intent("bot: all set\nhuman: ok", executed_actions=BOOKING), "+15550001", "+15550001", "current-job", None)
    assert jobs(session, "+15550001") == []


def test_booked_keeps_the_urgency_callback_with_time_had_everywhere_else():
    from app.core.escalation_guard import _URGENT_INTENTS
    from app.services.alerting import _SALES_QUEUE_URGENT_INTENTS
    from app.services.dashboard_metrics import _INTENT_SCORES
    assert "booked" in _URGENT_INTENTS and "booked" in _SALES_QUEUE_URGENT_INTENTS
    assert _INTENT_SCORES["booked"] == _INTENT_SCORES["callback_with_time"]
    assert "booked" in ia._HANDLERS


# -- callback detection reads the person's lines only ---------------------------------------------------------------------
def test_human_text_returns_only_the_person_lines_and_none_for_plain_text():
    assert human_text("bot: hi there\nhuman: not now\nbot: ok\nhuman: bye") == "not now bye"
    assert human_text("please try again") is None


def test_words_only_the_bot_said_do_not_create_a_callback():
    t = "bot: I can call you back tomorrow at 3 if that is easier?\nhuman: no that is fine, thanks"
    r = detect_intent(t, duration_seconds=40)
    assert r is None or r["intent"] not in ("callback_with_time", "callback_request", "call_later_no_time")


@pytest.mark.parametrize("t,want", [
    ("bot: Hi\nhuman: I am busy, please call me back tomorrow at 3.", ("callback_with_time", "callback_request")),
    ("bot: Hi\nhuman: call me later", ("callback_request", "call_later_no_time")),
    ("please try again", ("call_later_no_time",)),                       # plain text (no speaker labels) keeps matching everything
])
def test_a_real_request_from_the_person_is_still_detected(t, want):
    assert detect_intent(t, duration_seconds=40)["intent"] in want


# -- answering-machine greetings ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("t,want", [
    ("Please leave a message after the tone and we will call you back.", True),
    ("The person you are trying to reach is not available right now. Press 1 to leave a message.", True),
    ("Hi you've reached Sam, I can't come to the phone, leave your name and number.", True),
    ("yes this is Sam, go ahead", False),
    ("I check my voicemail rarely, call me back at 3 tomorrow. " + "I run a business and we are busy. " * 12, False),     # a long real conversation
    ("", False), (None, False),
])
def test_is_voicemail_greeting(t, want):
    assert is_voicemail_greeting(t) is want


def test_a_voicemail_greeting_produces_no_intent_instead_of_a_callback():
    t = "bot: Hi, this is Cora\nhuman: Please leave a message after the tone and we will call you back."
    assert detect_intent(t, duration_seconds=25) is None


def test_the_bot_leaving_a_message_does_not_count_as_a_greeting():
    t = "bot: I'm sorry I missed you, please leave a message or call us back\nhuman: hello? yes I can talk now"
    r = detect_intent(t, duration_seconds=60)
    assert r is None or r["intent"] != "voicemail_greeting"


# -- scheduled callbacks keep the lead's campaign ----------------------------------------------------------------------------
@pytest.mark.parametrize("handler,campaign", [(ia._handle_callback_request, "Cold Lead"), (ia._handle_call_later_no_time, "New Lead"),
                                              (ia._handle_callback_with_time, "Cold Lead")])
def test_a_callback_job_carries_the_leads_campaign(session, handler, campaign):
    add_lead(session, "+15550002", campaign)
    handler(session, "+15550002", "+15550002", {"datetime": None}, None)
    j = jobs(session, "+15550002")
    assert len(j) == 1 and j[0].payload_json["campaign_name"] == campaign and j[0].payload_json["phone_number"] == "+15550002"


def test_a_callback_for_an_unknown_lead_has_no_campaign_and_still_schedules(session):
    ia._handle_callback_request(session, "+15559999", "+15559999", {}, None)
    j = jobs(session, "+15559999")
    assert len(j) == 1 and "campaign_name" not in j[0].payload_json


def test_the_launcher_falls_back_to_the_leads_own_campaign(session):
    from app.worker.jobs.outbound_jobs import _lead_campaign_name
    add_lead(session, "+15550003", "Cold Lead")
    assert _lead_campaign_name(session, "+15550003") == "Cold Lead"
    assert _lead_campaign_name(session, "+15550004") is None
    assert _lead_campaign_name(session, "") is None


# -- relabel script for past bookings ---------------------------------------------------------------------------------------
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

from app.models.call_event import CallEvent  # noqa: E402

_spec = importlib.util.spec_from_file_location("relabel_booked", Path(__file__).resolve().parents[2] / "execution" / "relabel_booked_calls.py")
RELABEL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RELABEL)


def add_call(session, intent, executed_actions):
    session.add(CallEvent(id=str(uuid.uuid4()), call_id=str(uuid.uuid4()), contact_id="c", direction="outbound", status="completed",
                          dedupe_key=uuid.uuid4().hex, raw_payload_json={"executed_actions": executed_actions}, detected_intent=intent, created_at=NOW))
    session.flush()


def test_relabel_dry_run_changes_nothing_and_apply_only_touches_real_bookings(session):
    add_call(session, "callback_with_time", BOOKING)                                    # a booking recorded as a callback -> relabel
    add_call(session, "callback_with_time", {})                                         # a real callback request -> untouched
    add_call(session, "callback_with_time", {"action_ghl_create_booking": {"name": "b", "action_type": "ghl_booking_action_type",
                                                                           "return_value": json.dumps({"status": "error"})}})   # failed booking -> untouched
    add_call(session, "callback_request", BOOKING)                                      # other intent -> untouched
    lines = []
    assert RELABEL.run(session, False, lines.append)["relabelled"] == 0 and "DRY RUN" in lines[-1]
    assert session.scalars(select(CallEvent).where(CallEvent.detected_intent == "booked")).all() == []
    res = RELABEL.run(session, True, lambda *_: None)
    assert res["total"] == 3 and res["booked"] == 1 and res["relabelled"] == 1
    assert [c.detected_intent for c in session.scalars(select(CallEvent).where(CallEvent.detected_intent == "booked")).all()] == ["booked"]
    assert len(session.scalars(select(CallEvent).where(CallEvent.detected_intent == "callback_with_time")).all()) == 2
    assert len(session.scalars(select(CallEvent).where(CallEvent.detected_intent == "callback_request")).all()) == 1
