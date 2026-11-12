"""
Daily digest of what the content audit CLEARED - pure logic, no I/O (Ali approved 2026-11-11, spec/35).

The audit has two steps: a word check flags a message, then an AI meaning check reads it twice and may clear it. Cleared messages
raise no alert, so a wrong clearance would be invisible. This digest lists every message cleared on the previous Central day, with
its text, the matched words and the AI's reason, so a person reads them daily.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TIMEZONE = "America/Chicago"
SEND_FROM_HOUR = 8                      # Central: the digest for yesterday goes out from 8 AM
ALI = "ali@colaberry.com"
MAX_LISTED = 60                         # a digest nobody can face reading is no digest: the rest are counted, not listed
TEXT_CHARS = 600


def digest_day(now: datetime) -> date | None:
    """The Central day to report on (yesterday), or None before SEND_FROM_HOUR Central."""
    local = now.astimezone(ZoneInfo(TIMEZONE))
    if local.hour < SEND_FROM_HOUR:
        return None
    return local.date() - timedelta(days=1)


def window(day: date) -> tuple[datetime, datetime]:
    """[start, end) of a Central day, as timezone-aware datetimes."""
    tz = ZoneInfo(TIMEZONE)
    return datetime.combine(day, time.min, tz), datetime.combine(day + timedelta(days=1), time.min, tz)


def _words(terms: Any) -> str:
    if isinstance(terms, (list, tuple)):
        return ", ".join(str(t) for t in terms)
    return str(terms or "")


def _one(n: int, item: dict) -> str:
    at = item.get("at")
    when = at.astimezone(ZoneInfo(TIMEZONE)).strftime("%H:%M") if isinstance(at, datetime) else "?"
    text = " ".join(str(item.get("text") or "(text not stored; open the message in GHL)").split())[:TEXT_CHARS]
    votes = item.get("votes")
    return (f"{n}. {when} Central | {item.get('channel') or '?'} | contact {item.get('contact_id') or '?'}\n"
            f"   Words that flagged it: {_words(item.get('terms')) or '?'}\n"
            f"   AI said (reads: {', '.join(votes) if isinstance(votes, list) and votes else '?'}): {item.get('reason') or '(no reason stored)'}\n"
            f"   Text: {text}")


def build_digest(day: date, items: list[dict]) -> tuple[str, str]:
    """(subject, plain-text body). `items` are the messages cleared that day, oldest first."""
    label = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}"
    if not items:
        return (f"Cora audit digest: nothing cleared on {day.isoformat()}",
                f"No message was cleared by the AI meaning check on {label} (Central time).\n\n"
                "That means every message the word check flagged was either held for a person or none was flagged. "
                "This email is sent every day so you know the check is running.")
    listed = items[:MAX_LISTED]
    more = len(items) - len(listed)
    head = (f"{len(items)} message(s) were flagged by the word check and then cleared by the AI meaning check on {label} (Central time).\n"
            "Cleared messages raise no alert, so please read them. If one should not have been cleared, tell Kes.\n"
            "Each one was read twice by the AI and both reads said it breaks no rule.\n")
    body = head + "\n" + "\n\n".join(_one(i, it) for i, it in enumerate(listed, 1))
    if more > 0:
        body += f"\n\n...and {more} more not listed here (the list is capped at {MAX_LISTED}). Ask Kes for the full list."
    return f"Cora audit digest: {len(items)} cleared on {day.isoformat()}", body
