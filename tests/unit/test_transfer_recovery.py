"""Failed-transfer recovery (spec/39): a caller asked for a human, no transfer connected, no callback booked -> staff task now.

Payload shapes below are the real Synthflow values seen in production on 2026-10-23 (call 735331a5...)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import transfer_recovery as tr
from app.core.intent_detection import _extract_executed_actions, detect_intent
from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.models.task_event import TaskEvent

CARRIER_BLOCKED = {"admissions_transfer_roselen": {
    "name": "admissions_transfer_roselen", "action_type": "transfer_call_action_type",
    "return_value": json.dumps({"failed_reason": "transfer_blocked_by_carrier", "status": "transfer-failed-connection-error"})}}
OUTSIDE_HOURS = {"customer_support_bala": {
    "name": "customer_support_bala", "action_type": "transfer_call_action_type",
    "return_value": json.dumps({"failed_reason": "transfer_outside_availability", "status": "transfer-failed-connection-error"})}}
SUCCESS = {"taiwo_ipbc_payment": {
    "name": "taiwo_ipbc_payment", "action_type": "transfer_call_action_type",
    "return_value": json.dumps({"failed_reason": "", "status": "transfer-success"})}}
BOOKED = {"book": {"name": "book", "action_type": "booking_action_type", "return_value": json.dumps({"status": "success"})}}
RAG_ONLY = {"rag_action": {"name": "rag_action", "action_type": "rag_action_type", "return_value": "{}"}}
TRANSCRIPT = "bot: Hi\nhuman: I need to talk to a human, please connect me to a representative right now. " * 3


# -- the extractor reads the real status strings ----------------------------------------------------------------
@pytest.mark.parametrize("ea,reason", [(CARRIER_BLOCKED, "transfer_blocked_by_carrier"),
                                       (OUTSIDE_HOURS, "transfer_outside_availability")])
def test_real_failed_transfer_statuses_are_recognised(ea, reason):
    f = _extract_executed_actions(ea)
    assert f["transfer_attempted"] and f["transfer_failed"] and not f["transfer_succeeded"] and f["transfer_reason"] == reason


def test_a_connected_transfer_is_a_success_not_a_failure():
    f = _extract_executed_actions(SUCCESS)
    assert f["transfer_succeeded"] and not f["transfer_failed"]


def test_failure_then_a_successful_retry_counts_as_connected():
    both = {**CARRIER_BLOCKED, **SUCCESS}
    f = _extract_executed_actions(both)
    assert f["transfer_failed"] and f["transfer_succeeded"]
    assert tr.needs_callback("human_transfer_request", both) is False


# -- intent detection: a failed transfer is not a live handoff ---------------------------------------------------
def test_failed_transfer_is_not_reported_as_a_confirmed_transfer():
    r = detect_intent(TRANSCRIPT, executed_actions=CARRIER_BLOCKED)
    assert r["intent"] == "human_transfer_request"
    e = r["entities"]
    assert e["transfer_attempted"] is False and e["transfer_failed"] is True and e["transfer_reason"] == "transfer_blocked_by_carrier"


def test_connected_transfer_is_still_a_confirmed_transfer():
    r = detect_intent(TRANSCRIPT, executed_actions=SUCCESS)
    assert r["intent"] == "human_transfer_request" and r["entities"]["transfer_attempted"] is True


# -- when is a staff callback needed -----------------------------------------------------------------------------
@pytest.mark.parametrize("intent,ea,want", [
    ("human_transfer_request", CARRIER_BLOCKED, True),
    ("human_transfer_request", OUTSIDE_HOURS, True),
    (None, CARRIER_BLOCKED, True),                                      # a failed transfer is proof a human was asked for
    ("human_transfer_request", RAG_ONLY, True),                         # asked for a human, the agent never tried a transfer
    ("human_transfer_request", None, True),
    ("human_transfer_request", SUCCESS, False),                         # a person took the call
    ("human_transfer_request", {**CARRIER_BLOCKED, **BOOKED}, False),  # a callback was booked on the call
    ("interested_not_now", RAG_ONLY, False),
    (None, None, False),
])
def test_needs_callback(intent, ea, want):
    assert tr.needs_callback(intent, ea) is want


IPBC_FAIL = {"taiwo_ipbc_payment": {"name": "taiwo_ipbc_payment", "action_type": "transfer_x",
                                    "return_value": json.dumps({"failed_reason": "x"})}}


@pytest.mark.parametrize("ea,kind", [(CARRIER_BLOCKED, "admissions"), (OUTSIDE_HOURS, "support"), (RAG_ONLY, "admissions"),
                                     (IPBC_FAIL, "ipbc")])
def test_target_kind_follows_who_the_caller_asked_for(ea, kind):
    assert tr.target_kind(ea) == kind


def test_task_text_names_the_lead_the_reason_and_is_due_now():
    now = datetime(2026, 10, 23, 17, 50, tzinfo=timezone.utc)
    t = tr.build_task("Suresh", "+14692542085", CARRIER_BLOCKED, now=now)
    assert "Suresh" in t["title"] and "CALL BACK NOW" in t["title"]
    assert "transfer_blocked_by_carrier" in t["description"] and "+14692542085" in t["description"]
    assert t["due_date"] == now.isoformat() and "free" not in (t["title"] + t["description"]).lower()


# -- the injected voice prompts carry the hard rule --------------------------------------------------------------
@pytest.mark.parametrize("campaign", ["Cold Lead", "New Lead"])
def test_injected_outbound_prompts_start_with_the_failed_transfer_rule(campaign):
    from app.adapters.synthflow import _load_campaign_prompt
    p = _load_campaign_prompt(campaign)
    assert "## 0. HARD RULE" in p and p.index("## 0. HARD RULE") < p.index("## 1. BACKGROUND")
    assert "Never end the call" in p and "follow Section 0 above" in p
    assert 'Never say** "try again later"' in p                         # the forbidden phrase is named so the model avoids it


# -- create_crm_task forces the callback task --------------------------------------------------------------------
@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s
    eng.dispose()


def _analysis(create_task=False):
    from app.core.ai_message_generator import GhlCallAnalysisResult
    return GhlCallAnalysisResult(
        task_title="ai title", task_description="ai desc", assign_to="aiassignee", is_lead_classification=True,
        lead_classification="warm_lead", create_task=create_task, outbound_call_details="", call_detailed_summary="",
        ai_campaign="No", call_start_time_formatted="", task_due_date="2026-10-24T00:00:00Z")


def _run_job(session, ea, intent, create_task=False):
    ev = CallEvent(id=str(uuid.uuid4()), call_id="c1", contact_id="contact-1", direction="outbound", status="completed",
                   dedupe_key=f"c1:{uuid.uuid4().hex}", raw_payload_json={"executed_actions": ea}, lead_name="Suresh",
                   detected_intent=intent, created_at=datetime.now(tz=timezone.utc))
    session.add(ev)
    job = ScheduledJob(id=str(uuid.uuid4()), job_type="create_crm_task", entity_type="call", entity_id="c1", status="pending",
                       run_at=datetime.now(tz=timezone.utc), payload_json={"call_id": "c1", "call_event_id": ev.id},
                       created_at=datetime.now(tz=timezone.utc), version=0)
    session.add(job)
    session.flush()
    created = MagicMock(return_value={"shadow": True})
    with patch("app.worker.jobs.crm_jobs.get_sync_session") as ms, \
            patch("app.core.ai_message_generator.generate_ghl_call_analysis", return_value=_analysis(create_task)), \
            patch("app.adapters.ghl.GHLClient.create_task", created), \
            patch("app.adapters.ghl.GHLClient.search_contact_by_phone", return_value=None):
        ms.return_value.__enter__ = lambda _: session
        ms.return_value.__exit__ = MagicMock(return_value=False)
        from app.worker.jobs.crm_jobs import create_crm_task
        create_crm_task(job.id)
    return created, ev


def test_failed_transfer_forces_a_callback_task_even_when_the_ai_analysis_wanted_none(session):
    created, ev = _run_job(session, CARRIER_BLOCKED, "human_transfer_request")
    assert created.call_count == 1
    kw = created.call_args.kwargs
    assert "CALL BACK NOW" in kw["title"] and "Suresh" in kw["title"] and kw["assigned_to"] and kw["assigned_to"] != "aiassignee"
    assert session.scalars(select(TaskEvent).where(TaskEvent.call_event_id == ev.id)).first().status == "created"


def test_a_connected_transfer_does_not_force_a_task(session):
    created, _ = _run_job(session, SUCCESS, "human_transfer_request")
    assert created.call_count == 0


def test_an_existing_ai_task_is_left_alone(session):
    created, _ = _run_job(session, CARRIER_BLOCKED, "human_transfer_request", create_task=True)
    assert created.call_args.kwargs["title"] == "ai title"
