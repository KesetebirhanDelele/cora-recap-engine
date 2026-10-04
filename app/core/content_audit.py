"""
Content audit of what GHL actually sent (pure logic; spec/35 follow-up, Ali 2026-10-03).

Two things are flagged on every outbound text / email GHL reports, whoever sent it (Cora's workflows, the Conversation AI
assistant, staff in the app):
  1. a forbidden term: a retired course name or an employment / placement / job-guarantee claim (app/core/offer.py list)
  2. an opt-out reply answered by a sales message: the lead said stop and the next outbound text within 3 minutes is not an
     acknowledgement of the opt-out.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from app.core import offer
from app.core import optout as oo

ANSWER_WINDOW = timedelta(minutes=3)
# A reply that CONFIRMS the opt-out is fine: it must not be flagged.
_ACK = re.compile(r"(?i)\b(unsubscrib\w+|stop messaging|will stop|no longer (?:receive|message|contact)|opted out|"
                  r"won't (?:receive|message|contact)|will not (?:receive|message|contact)|removed you|sorry for the bother)\b")


def forbidden_in(body: str | None, terms: tuple[str, ...]) -> list[str]:
    return offer.forbidden_hits(body, terms)


def answered_opt_out(inbound_body: str | None, outbound_body: str | None) -> bool:
    """True when the inbound text is an opt-out and the outbound text that followed does not acknowledge it."""
    verdict = oo.classify(inbound_body, "sms")
    if not (verdict.kind == oo.DND and verdict.scope):
        return False
    return not _ACK.search(outbound_body or "")


def opt_out_answers(msgs: list[dict], parse_ts: Any) -> list[dict]:
    """Outbound texts in a conversation that answered an opt-out reply within ANSWER_WINDOW without acknowledging it."""
    flagged = []
    ordered = sorted((m for m in msgs if m.get("dateAdded")), key=lambda m: m["dateAdded"])
    for i, m in enumerate(ordered):
        if (m.get("direction") or "").lower() != "inbound" or m.get("messageType") != "TYPE_SMS":
            continue
        at: datetime | None = parse_ts(m.get("dateAdded"))
        if at is None:
            continue
        for later in ordered[i + 1:]:
            lat = parse_ts(later.get("dateAdded"))
            if lat is None or lat - at > ANSWER_WINDOW:
                break
            if (later.get("direction") or "").lower() == "outbound" and later.get("messageType") == "TYPE_SMS":
                if answered_opt_out(m.get("body"), later.get("body")):
                    flagged.append(later)
                break
    return flagged
