"""Content audit (spec/35 follow-up): retired-course / employment claims and answered opt-outs in what GHL sent."""
from __future__ import annotations

from app.core import content_audit as ca
from app.core import offer
from app.services.delivery_sync import _parse_ts

TERMS = offer.parse_terms(None)


def test_retired_course_and_employment_claims_are_flagged():
    assert "data analytics" in ca.forbidden_in("What sparked your interest in data analytics?", TERMS)
    assert "employment rate" in ca.forbidden_in("Our program has a 71% employment rate.", TERMS)
    assert ca.forbidden_in("Colaberry helps learners prepare for AI roles.", TERMS) == []
    assert ca.forbidden_in(None, TERMS) == []


def _m(at, direction, body, typ="TYPE_SMS", i=None):
    return {"id": i or f"{direction}-{at}", "dateAdded": f"2026-10-03T20:{at}:00.000Z", "direction": direction,
            "messageType": typ, "body": body}


def test_a_sales_reply_to_a_stop_is_flagged_but_an_acknowledgement_is_not():
    thread = [_m("00", "inbound", "STOP"), _m("01", "outbound", "Great question! Pay varies a lot.", i="bad")]
    assert [m["id"] for m in ca.opt_out_answers(thread, _parse_ts)] == ["bad"]
    ok = [_m("00", "inbound", "STOP"), _m("01", "outbound", "Understood, I will stop messaging you. Sorry for the bother.")]
    assert ca.opt_out_answers(ok, _parse_ts) == []
    gh = [_m("00", "inbound", "Do not contact me"), _m("01", "outbound", "You have been unsubscribed and will no longer receive messages.")]
    assert ca.opt_out_answers(gh, _parse_ts) == []


def test_only_the_first_outbound_within_three_minutes_counts_and_ordinary_replies_are_ignored():
    late = [_m("00", "inbound", "STOP"), _m("05", "outbound", "Hello again", i="late")]
    assert ca.opt_out_answers(late, _parse_ts) == []                       # answered after the 3-minute window
    normal = [_m("00", "inbound", "I am on PST please"), _m("01", "outbound", "Thanks for letting me know!")]
    assert ca.opt_out_answers(normal, _parse_ts) == []
    email = [_m("00", "inbound", "STOP", typ="TYPE_EMAIL"), _m("01", "outbound", "Sales text", i="x")]
    assert ca.opt_out_answers(email, _parse_ts) == []                      # only texts are judged here
