"""
Voice-call audit (pure logic; spec/35 "voice audit", Kes 2026-10-20).

GHL's AI Receptionist (a Voice AI agent) talks to callers without Cora seeing it. Its call log carries a transcript of
`human:` and `bot:` lines. Only what the BOT said is audited: a caller who asks for a SQL course or says "free" is not a
violation; the agent offering, pricing or confirming a retired course, saying "free", or making an employment claim is.

Same word list and exemptions as texts / emails (app/core/offer.py), plus the SPOKEN form of the approved sign-up link
("training dot colaberry dot com slash start dash free"), because a transcript writes what was said aloud.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core import content_audit as ca
from app.core import offer

# GHL Voice AI agent id -> name shown in alerts. Unknown agents are still audited and labelled by the tail of their id.
KNOWN_AGENTS = {"6a97318a65c4b663f8b154a1": "AI Receptionist"}
ORIGIN_RECEPTIONIST = "GHL AI Receptionist (voice call)"

_TURN = re.compile(r"^\s*(human|bot)\s*:\s*(.*)$", re.IGNORECASE)
_SPOKEN_APPROVED_LINK = re.compile(
    r"(?i)training\s*(?:dot|\.)\s*colaberry\s*(?:dot|\.)\s*com\s*(?:slash|/)\s*start\s*(?:dash|hyphen|-)\s*free(?![a-z0-9])(?!\s*(?:dash|hyphen|-))")


def origin_label(agent_id: str | None) -> str:
    """Which system produced the call, for the alert line (so a person fixes the right place)."""
    name = KNOWN_AGENTS.get(agent_id or "")
    if name == "AI Receptionist":
        return ORIGIN_RECEPTIONIST
    tail = agent_id[-6:] if agent_id else "unknown"
    return f"GHL Voice AI agent ...{tail} (voice call)"


def parse_transcript(raw: str | None) -> list[tuple[str, str]]:
    """[(speaker, text)] with speaker 'human' or 'bot'. Lines without a prefix continue the previous turn."""
    turns: list[list[str]] = []
    for line in (raw or "").splitlines():
        m = _TURN.match(line)
        if m:
            turns.append([m.group(1).lower(), m.group(2)])
        elif turns and line.strip():
            turns[-1][1] += " " + line.strip()
    return [(who, txt.strip()) for who, txt in turns]


def bot_turns(raw: str | None) -> list[str]:
    return [txt for who, txt in parse_transcript(raw) if who == "bot" and txt]


def _scannable(text: str, brand_phrases: tuple[str, ...]) -> str:
    return _SPOKEN_APPROVED_LINK.sub(" ", ca.strip_brand(text, brand_phrases))


@dataclass(frozen=True)
class Finding:
    terms: list[str]
    excerpt: str            # the first bot turn that triggered a hit
    prior_human: str | None  # what the caller said just before it (context for the judge)
    course_only: bool       # every hit is a course name: the offer/not-offer judge may clear it


def scan_call(raw_transcript: str | None, terms: tuple[str, ...], brand_phrases: tuple[str, ...] = ()) -> Finding | None:
    """None when nothing the bot said is flagged. A hard term ("free", an employment claim) is never cleared by the judge."""
    turns = parse_transcript(raw_transcript)
    all_hits: list[str] = []
    excerpt = ""
    prior: str | None = None
    last_human: str | None = None
    for who, txt in turns:
        if who == "human":
            last_human = txt
            continue
        hits = offer.forbidden_hits(_scannable(txt, brand_phrases), terms)
        if not hits:
            continue
        if not excerpt:
            excerpt, prior = txt[:200], last_human
        all_hits += [h for h in hits if h not in all_hits]
    if not all_hits:
        return None
    return Finding(terms=all_hits, excerpt=excerpt, prior_human=prior, course_only=ca.course_only(all_hits))
