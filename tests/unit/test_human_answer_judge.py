"""Live person vs voicemail judge (spec/49 addendum, 2026-11-19). No network: the OpenAI client is a stub."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core import human_answer_judge as J
from app.models.base import Base
from app.models.staff_call_quality import StaffCallQuality as S

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
VM = ("I'll see if this person is available. I'm sorry, this person is not available. Please leave a message "
      "after the tone. Hello there, this is Rose from Colaberry.")
LIVE = "Hello, this is Rose from Colaberry. How are you? Fine, thank you. Actually I'm not interested."


def client(label="human", raises=None):
    c = MagicMock()
    if raises:
        c.chat_completion.side_effect = raises
    else:
        c.chat_completion.return_value = {"label": label, "reason": "x"}
    return c


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[S.__table__])
    with sessionmaker(bind=engine)() as s:
        yield s


def add(session, text, *, direction="outbound", connected=True, verdict=None, when=None, cid="C1"):
    row = S(id=str(uuid.uuid4()), ghl_message_id=str(uuid.uuid4()), ghl_contact_id=cid, call_time=when or NOW,
            call_connected=connected, direction=direction, transcript_text=text, answered_by_human=verdict)
    session.add(row)
    session.commit()
    return row


# ── the judge ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", [("human", True), ("voicemail", False), ("unclear", False),
                                            ("garbage", None), (None, None)])
def test_judge_maps_labels_fail_safe(label, expected):
    assert J.judge_human_answer(LIVE, client(label), "m") is expected


def test_judge_error_is_none_not_a_guess():
    assert J.judge_human_answer(LIVE, client(raises=RuntimeError("quota")), "m") is None


def test_judge_non_dict_answer_is_none():
    c = MagicMock()
    c.chat_completion.return_value = ["human"]
    assert J.judge_human_answer(LIVE, c, "m") is None


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_transcript_is_false_without_calling_the_model(empty):
    c = client()
    assert J.judge_human_answer(empty, c, "m") is False
    c.chat_completion.assert_not_called()


def test_transcript_is_capped_before_sending():
    c = client()
    J.judge_human_answer("x" * 50000, c, "m")
    sent = c.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert len(sent) <= len(J.PROMPT) + J.TRANSCRIPT_CAP


# ── judge_pending ──────────────────────────────────────────────────────────────

def settings():
    return SimpleNamespace(openai_model_human_answer="m")


def test_pending_judges_only_outbound_connected_unjudged_with_transcript(session):
    todo = add(session, LIVE)
    add(session, LIVE, direction="inbound")                  # inbound: never judged
    add(session, LIVE, connected=False)                      # not connected
    add(session, LIVE, verdict=False)                        # already judged: not repeated
    add(session, "", direction="outbound")                   # no transcript
    add(session, LIVE, when=NOW - timedelta(days=60))        # outside the window
    c = client("human")
    counts = J.judge_pending(session, settings(), since=NOW - timedelta(days=30), client=c)
    assert counts == {"judged": 1, "human": 1, "not_human": 0, "failed": 0}
    assert c.chat_completion.call_count == 1
    assert session.get(S, todo.id).answered_by_human is True


def test_pending_failure_stays_null_and_is_retried_next_run(session):
    row = add(session, VM)
    since = NOW - timedelta(days=30)
    c1 = client(raises=RuntimeError("down"))
    assert J.judge_pending(session, settings(), since=since, client=c1)["failed"] == 1
    assert session.get(S, row.id).answered_by_human is None
    c2 = client("voicemail")
    assert J.judge_pending(session, settings(), since=since, client=c2)["not_human"] == 1
    assert session.get(S, row.id).answered_by_human is False


def test_pending_is_idempotent_and_bounded(session):
    for i in range(5):
        add(session, LIVE, cid=f"C{i}")
    since = NOW - timedelta(days=30)
    assert J.judge_pending(session, settings(), since=since, limit=3, client=client())["judged"] == 3
    assert J.judge_pending(session, settings(), since=since, limit=3, client=client())["judged"] == 2
    c = client()
    assert J.judge_pending(session, settings(), since=since, client=c)["judged"] == 0
    c.chat_completion.assert_not_called()


# ── the shared "reached" rule ──────────────────────────────────────────────────

def test_reached_requires_outbound_connected_and_a_live_person(session):
    add(session, LIVE, verdict=True, cid="REAL")
    add(session, VM, verdict=False, cid="VOICEMAIL")
    add(session, LIVE, verdict=None, cid="UNJUDGED")
    add(session, LIVE, verdict=True, direction="inbound", cid="INBOUND")
    add(session, LIVE, verdict=True, connected=False, cid="NOTCONNECTED")
    got = session.execute(select(S.ghl_contact_id).where(*J.reached_conditions(S))).scalars().all()
    assert got == ["REAL"]


# ── scan hook ──────────────────────────────────────────────────────────────────

def _scan(session, direction, label):
    from unittest.mock import patch

    from app.worker.jobs.staff_call_quality_jobs import _process_call_message

    conv = MagicMock()
    conv.get_message_transcription.return_value = [{"transcript": LIVE}]
    contact = MagicMock()
    contact.get_contact.return_value = {"contact": {"tags": ["warm lead"], "customFields": []}}
    msg = {"id": f"msg-{direction}", "direction": direction, "userId": "U", "dateAdded": "2026-11-19T12:00:00Z",
           "meta": {"call": {"duration": 60, "status": "completed"}}}
    with patch("app.adapters.openai_client.OpenAIClient") as Mock:
        inst = MagicMock()
        inst.chat_completion.side_effect = lambda **k: (
            {"label": label} if "LIVE HUMAN" in k["messages"][0]["content"] else {"overall_score": 70, "summary": "ok"})
        Mock.return_value = inst
        _process_call_message(session, conv, contact, MagicMock(), conv_id="c", contact_id="g", phone=None,
                              message=msg)
    return session.scalars(select(S).where(S.ghl_message_id == f"msg-{direction}")).one()


def test_scan_records_the_verdict_for_outbound_calls(session):
    assert _scan(session, "outbound", "voicemail").answered_by_human is False
    session.rollback()


def test_scan_leaves_inbound_calls_unjudged(session):
    assert _scan(session, "inbound", "human").answered_by_human is None
