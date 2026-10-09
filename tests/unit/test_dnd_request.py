"""A lead on DND asks to be contacted: a person is alerted, Cora does not override (Kes 2026-10-09)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.contact_request import asks_to_be_contacted
from app.services import dnd_request


@pytest.mark.parametrize("text", [
    "Please call me back tomorrow", "can you call me at 3", "Give me a call", "send me more information",
    "I'd like more info about the program", "text me the link", "I'm interested", "ready to enroll", "email me the details",
])
def test_requests_to_be_contacted_are_recognised(text):
    assert asks_to_be_contacted(text) is True


@pytest.mark.parametrize("text", [
    "STOP", "Don't call me", "stop texting me, I'm not interested", "do not contact me again", "wrong number",
    "Thanks", "ok", "", None, "I never received the email",
])
def test_stops_negations_and_chatter_are_not_requests(text):
    assert asks_to_be_contacted(text) is False


def test_a_stop_and_a_request_in_one_reply_is_a_stop():
    assert asks_to_be_contacted("Stop calling me, just email me the info") is False


def test_quoted_history_and_footers_do_not_count():
    body = "Thanks\n\nOn Mon, Oct 5, 2026 at 9:00 AM Cora wrote:\n> Please call me back if you are interested"
    assert asks_to_be_contacted(body, "email") is False


def _session(existing=False):
    s = MagicMock()
    s.execute.return_value.fetchone.return_value = (1,) if existing else None
    return s


def test_alert_names_the_lead_the_words_and_the_next_steps_and_is_sent_once():
    sent = []
    with patch("app.services.channel_health._lead_line", lambda st, cid: f"Jo Lead, phone +15551230000, email jo@x.com, contact {cid}"), \
            patch("app.services.alerting._send_alert_email", lambda **kw: sent.append(kw)):
        assert dnd_request.notify(_session(), object(), key="msg:m1", contact_id="c1", how="asked by sms to be contacted",
                                  words="please call me back", why_blocked="GHL DND is on for sms") is True
        assert dnd_request.notify(_session(existing=True), object(), key="msg:m1", contact_id="c1", how="x", words="y",
                                  why_blocked="z") is False
    assert len(sent) == 1
    m = sent[0]["message"]
    assert sent[0]["alert_type"] == "dnd_contact_request:msg:m1"
    assert "+15551230000" in m and "please call me back" in m and "Cora has NOT contacted them" in m and "GHL DND is on for sms" in m


def test_only_a_blocked_callback_triggers_the_call_gate_alert():
    with patch.object(dnd_request, "notify") as n, patch.object(dnd_request, "last_words", return_value="call me"):
        dnd_request.on_blocked_callback(MagicMock(), object(), SimpleNamespace(id="j1", payload_json={"intent_reason": "callback_request"}),
                                        "c1", "GHL Call do-not-disturb is active")
        assert n.call_count == 1 and n.call_args.kwargs["key"] == "job:j1" and n.call_args.kwargs["words"] == "call me"
        dnd_request.on_blocked_callback(MagicMock(), object(), SimpleNamespace(id="j2", payload_json={"source": "campaign_entry:cold_lead"}),
                                        "c1", "x")
        assert n.call_count == 1                                           # an ordinary campaign call is not a lead request


def test_reply_alerts_only_when_the_lead_is_on_dnd():
    contact_dnd = {"contact": {"dnd": False, "dndSettings": {"SMS": {"status": "active"}}, "tags": []}}
    contact_ok = {"contact": {"dnd": False, "dndSettings": {}, "tags": []}}
    for record, expect in ((contact_dnd, 1), (contact_ok, 0)):
        ghl = MagicMock()
        ghl.get_contact.return_value = record
        with patch("app.services.optout._ghl", return_value=ghl), patch.object(dnd_request, "notify") as n:
            dnd_request.on_reply(MagicMock(), object(), channel="sms", contact_id="c1", message_id="m1", body="please call me")
            assert n.call_count == expect
    with patch("app.services.optout._ghl") as g:                                # not a request: GHL is not even called
        dnd_request.on_reply(MagicMock(), object(), channel="sms", contact_id="c1", message_id="m2", body="STOP")
        assert g.call_count == 0
