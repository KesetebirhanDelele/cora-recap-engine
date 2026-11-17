"""
What a lead actually wrote in an email reply - pure logic, no I/O.

A reply carries the lead's new words on top and our own earlier email quoted underneath ("On <date>, <sender> wrote:" ...).
Analysing the whole body reads our own marketing text as if the lead had written it (2026-11-17: a lead answered an Open House
invite with "Stop" and Cora told staff they wanted to attend).
"""
from __future__ import annotations

import html
import re

from app.core.sms_eligibility import is_stop_reply

# Where the quoted original starts. Matched against the text with tags removed; the earliest hit wins.
_QUOTE_MARKERS = [
    re.compile(r"\bOn\s[^\n]{0,200}\bwrote:", re.IGNORECASE),
    re.compile(r"-{2,}\s*(?:Original|Forwarded) Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*From:\s.+\n\s*(?:Sent|Date):", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*>", re.MULTILINE),
    re.compile(r"^\s*Sent from my \w+", re.IGNORECASE | re.MULTILINE),
]
_BLOCK = re.compile(r"(?i)<\s*br\s*/?\s*>|</\s*(?:p|div|li|tr|h[1-6])\s*>")
_TAG = re.compile(r"<[^>]+>")


def visible_text(body: str | None) -> str:
    """Body with tags removed and one line per block."""
    text = _BLOCK.sub("\n", body or "")
    text = html.unescape(_TAG.sub(" ", text)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def reply_text(body: str | None) -> str:
    """Only the lead's own new words: everything before the quoted original. If a marker sits at the very start (nothing
    written above it) the whole text is returned, so a forwarded message is never reduced to an empty string."""
    text = visible_text(body)
    cut = len(text)
    for pattern in _QUOTE_MARKERS:
        m = pattern.search(text)
        if m and m.start() < cut:
            cut = m.start()
    head = text[:cut].strip()
    return head if head else text


def is_stop_request(body: str | None) -> bool:
    """True when the lead's own words open with a bare stop word ("Stop", "Unsubscribe", ...), ignoring a sign-off below it."""
    first = next((ln for ln in reply_text(body).splitlines() if ln.strip()), "")
    return is_stop_reply(first)
