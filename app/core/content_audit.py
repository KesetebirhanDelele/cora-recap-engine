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


_TAGS = re.compile(r"(?s)<(?:style|script)\b.*?</(?:style|script)>|<[^>]+>")
_EMPLOYMENT_TERM = re.compile(r"(?i)employment|placement|job guarantee|guaranteed job|hiring rate")


def forbidden_in(body: str | None, terms: tuple[str, ...]) -> list[str]:
    return offer.forbidden_hits(body, terms)


DEFAULT_BRAND_PHRASES = "Colaberry School of Data Analytics,School of Data Analytics"


def parse_brand_phrases(value: str | None) -> tuple[str, ...]:
    raw = DEFAULT_BRAND_PHRASES if value is None else value
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def strip_brand(body: str | None, phrases: tuple[str, ...]) -> str:
    """Remove the company's own name (e.g. in a staff email signature) before the keyword scan. Only the phrase is removed,
    so 'the Data Analytics bootcamp at Colaberry School of Data Analytics' still leaves 'Data Analytics bootcamp'."""
    text = body or ""
    for p in sorted(phrases, key=len, reverse=True):          # longest first: the full name before its shorter form
        text = re.sub(r"(?i)" + re.escape(p), " ", text)
    return text


def visible_text(body: str | None) -> str:
    """Body with HTML tags and entities-as-spaces removed, whitespace collapsed (emails arrive as HTML)."""
    return re.sub(r"\s+", " ", _TAGS.sub(" ", body or "").replace("&nbsp;", " ")).strip()


_FOOTER_SENTENCE = re.compile(
    r"(?i)unsubscrib|no longer (?:wish|want|care) to (?:receive|get)|manage (?:your )?(?:email )?(?:preferences|subscription)|"
    r"view (?:this )?(?:email )?in (?:your )?browser|reply stop|to opt[- ]out")


def strip_footer(text: str | None) -> str:
    """Drop unsubscribe / footer boilerplate sentences from already-visible text. Used ONLY for what the LLM judge reads: a
    footer line such as "if you no longer wish to receive these emails" otherwise reads as a decline of the course. The keyword
    scan still runs on the full text, so a forbidden term inside a footer is still found."""
    parts = re.split(r"(?<=[.!?])\s+|\[https?://[^\]]*\]", text or "")
    return " ".join(p for p in parts if p and not _FOOTER_SENTENCE.search(p)).strip()


def is_employment_term(term: str) -> bool:
    return bool(_EMPLOYMENT_TERM.search(term))


DEFAULT_INTERNAL_EMAIL_DOMAINS = "colaberry.com,trillionsquare.com"


def parse_internal_domains(value: str | None) -> tuple[str, ...]:
    """Domains whose mailboxes are our own staff. None -> default list; an empty string disables the exemption."""
    raw = DEFAULT_INTERNAL_EMAIL_DOMAINS if value is None else value
    return tuple(d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip())


def is_internal_email(address: str | None, domains: tuple[str, ...]) -> bool:
    """True only for a well-formed address on (or a subdomain of) one of `domains`. Anything else - missing, malformed,
    a look-alike such as "colaberry.com.evil.com" or "notcolaberry.com" - is NOT internal, so the message is audited."""
    a = (address or "").strip().lower()
    if a.count("@") != 1 or not domains:
        return False
    local, _, host = a.partition("@")
    if not local or "." not in host:
        return False
    return any(host == d or host.endswith("." + d) for d in domains)


def is_hard_term(term: str) -> bool:
    """An employment / placement claim or the banned word "free" (TWC advertising rule): always flagged, never sent to the
    LLM judge, because the judge only decides whether a message declines a retired COURSE."""
    return is_employment_term(term) or term.strip().lower() == "free"


def course_only(hits: list[str]) -> bool:
    """True when every hit is a course / subject name (none is an employment / placement claim or the word "free")."""
    return bool(hits) and not any(is_hard_term(h) for h in hits)


def prior_inbound(msgs: list[dict], message: dict, parse_ts: Any) -> str | None:
    """Body of the latest inbound message sent before `message` in the same conversation, if any."""
    at = parse_ts(message.get("dateAdded"))
    best: tuple[datetime, str] | None = None
    for m in msgs:
        t = parse_ts(m.get("dateAdded"))
        if t is None or at is None or t >= at or (m.get("direction") or "").lower() != "inbound":
            continue
        if best is None or t > best[0]:
            best = (t, m.get("body") or "")
    return best[1] if best else None


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
