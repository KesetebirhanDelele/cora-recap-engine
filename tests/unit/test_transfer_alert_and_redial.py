"""Failed transfer (spec/39): no AI redial is scheduled, pending redials are cancelled, and an alert names each failed call."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core import intent_actions as ia
from app.models import Base, ScheduledJob
from app.models.alert_event import AlertEvent
from app.models.call_event import CallEvent
from app.services import transfer_alert as ta

NOW = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)


def _ea(name, status, reason=""):
    return {name: {"name": name, "action_type": "transfer_call_action_type",
                   "return_value": json.dumps({"failed_reason": reason, "status": status})}}


BLOCKED = _ea("admissions_transfer_roselen", "transfer-failed-connection-error", "transfer_blocked_by_carrier")
OUTSIDE = _ea("customer_support_farhat", "transfer-failed-connection-error", "transfer_outside_availability")
OK = _ea("taiwo_ipbc_payment", "transfer-success")
BOOKED = {"action_ghl_create_booking": {"name": "action_ghl_create_booking", "action_type": "ghl_booking_action_type",
                                        "return_value": json.dumps({"status": "success"})}}


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s
    eng.dispose()


def _job(session, contact_id, status="pending", job_type="launch_outbound_call", reason="transfer_requested"):
    j = ScheduledJob(id=str(uuid.uuid4()), job_type=job_type, entity_type="lead", entity_id=contact_id, status=status,
                     run_at=NOW + timedelta(hours=2), payload_json={"contact_id": contact_id, "intent_reason": reason},
                     created_at=NOW, version=0)
    session.add(j)
    session.flush()
    return j


def _redials(session, contact_id):
    return session.scalars(select(ScheduledJob).where(
        ScheduledJob.job_type == "launch_outbound_call", ScheduledJob.payload_json["contact_id"].as_string() == contact_id)).all()


# -- no AI redial after a failed transfer ------------------------------------------------------------------------
def test_failed_transfer_schedules_no_redial_and_cancels_a_pending_one(session):
    old = _job(session, "c1")
    other = _job(session, "c1", job_type="create_crm_task")            # other jobs of the same lead are NOT touched
    ia._handle_human_transfer_request(session, "c1", "+15550001", {"transfer_attempted": False, "transfer_failed": True,
                                                                   "transfer_reason": "transfer_blocked_by_carrier"}, NS())
    assert session.get(ScheduledJob, old.id).status == "cancelled"
    assert session.get(ScheduledJob, other.id).status == "pending"
    assert [j.status for j in _redials(session, "c1")] == ["cancelled"]       # nothing new was scheduled


def test_a_redial_already_running_is_not_cancelled(session):
    running = _job(session, "c2", status="running")
    ia._handle_human_transfer_request(session, "c2", "+15550002", {"transfer_failed": True}, NS())
    assert session.get(ScheduledJob, running.id).status == "running"


def test_confirmed_transfer_still_schedules_nothing(session):
    ia._handle_human_transfer_request(session, "c3", "+15550003", {"transfer_attempted": True}, NS())
    assert _redials(session, "c3") == []


def test_unknown_outcome_keeps_the_existing_two_hour_redial(session):
    """No failed-transfer signal (the caller only asked for a human): existing behaviour is unchanged."""
    ia._handle_human_transfer_request(session, "c4", "+15550004", {}, NS())
    assert len(_redials(session, "c4")) == 1


# -- the alert ---------------------------------------------------------------------------------------------------
def _call(session, call_id, ea, name="Suresh", minutes_ago=30, campaign="Cold Lead"):
    session.add(CallEvent(id=str(uuid.uuid4()), call_id=call_id, contact_id="+14692542085", direction="outbound",
                          status="completed", dedupe_key=f"{call_id}:{uuid.uuid4().hex}", lead_name=name, campaign_name=campaign,
                          start_time_utc=NOW - timedelta(minutes=minutes_ago), created_at=NOW,
                          raw_payload_json={"executed_actions": ea, "phone_number_to": "+14692542085"}))
    session.flush()


def _check(session):
    sent = []
    with patch("app.services.alerting._send_alert_email", lambda **kw: sent.append(kw)):
        total = ta.run_transfer_failure_check(session, NS(), NOW)
    session.flush()                                                       # the alert cycle commits after each check
    return total, sent


def test_failed_transfer_raises_one_alert_naming_the_caller_and_the_reason(session):
    _call(session, "call-1", BLOCKED)
    total, sent = _check(session)
    assert total == 1 and len(sent) == 1
    msg = sent[0]["message"]
    assert sent[0]["alert_type"] == "call_transfer_failed" and sent[0]["severity"] == "warning"
    assert "Suresh" in msg and "+14692542085" in msg and "transfer_blocked_by_carrier" in msg and "carrier" in msg
    assert "callback booked on the call: NO" in msg and "call-1" in msg


def test_the_same_call_never_alerts_twice_even_after_resolve(session):
    _call(session, "call-1", BLOCKED)
    _check(session)
    session.execute(text("UPDATE alert_events SET status = 'resolved'"))
    total, sent = _check(session)
    assert total == 1 and sent == []


def test_a_new_failed_call_alerts_again_with_only_the_new_line(session):
    _call(session, "call-1", BLOCKED)
    _check(session)
    _call(session, "call-2", OUTSIDE, name="Maria", minutes_ago=5)
    total, sent = _check(session)
    assert total == 2 and len(sent) == 1
    assert "Maria" in sent[0]["message"] and "call-2" in sent[0]["message"] and "call-1" not in sent[0]["message"]
    assert "support" in sent[0]["message"]


def test_connected_transfers_and_other_calls_raise_nothing(session):
    _call(session, "ok-1", OK)
    _call(session, "ok-2", {**BLOCKED, **OK})                              # failed first, then connected
    _call(session, "none-1", {})
    total, sent = _check(session)
    assert total == 0 and sent == []


def test_the_alert_says_when_the_agent_did_book_a_callback(session):
    _call(session, "call-1", {**BLOCKED, **BOOKED})
    _, sent = _check(session)
    assert "callback booked on the call: yes" in sent[0]["message"]


def test_calls_older_than_24_hours_are_ignored(session):
    _call(session, "old-1", BLOCKED, minutes_ago=60 * 25)
    total, sent = _check(session)
    assert total == 0 and sent == []


def test_the_active_alert_resolves_itself_when_no_failed_transfer_is_left(session):
    _call(session, "call-1", BLOCKED)
    _check(session)
    later = NOW + timedelta(hours=25)
    with patch("app.services.alerting._send_alert_email", lambda **kw: None):
        assert ta.run_transfer_failure_check(session, NS(), later) == 0
    session.flush()
    session.expire_all()
    assert session.scalars(select(AlertEvent)).first().status == "resolved"


def test_outside_availability_with_a_booked_callback_is_not_alerted_but_without_one_it_is(session):
    _call(session, "ok-1", {**OUTSIDE, **BOOKED})
    total, sent = _check(session)
    assert total == 0 and sent == []
    _call(session, "bad-1", OUTSIDE)
    total, sent = _check(session)
    assert total == 1 and "bad-1" in sent[0]["message"]


def test_a_carrier_block_is_alerted_even_when_a_callback_was_booked(session):
    _call(session, "call-1", {**BLOCKED, **BOOKED})
    total, sent = _check(session)
    assert total == 1 and "callback booked on the call: yes" in sent[0]["message"]
