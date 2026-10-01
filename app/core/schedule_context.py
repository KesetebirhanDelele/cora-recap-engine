"""
Schedule context — pure logic, no I/O (spec/32).

The dashboard holds two dates: next class start and next open house. Once a
date has passed it must never be told to a lead again: the setting is cleared
(see app/services/date_expiry.py) and messages instead invite the lead to
start learning for free.

Terms
-----
"unset"  - blank, or one of the legacy placeholder words ("upcoming", "tbd"...).
           Means: nothing is scheduled. NOT a free-text date like "Q3 2026".
"passed" - the value parses to a concrete date WITH a year and that day is over.
           A year-less value ("Oct 29") is never auto-expired: we can't tell
           whether it means this year or next, so we don't guess.
"""
from __future__ import annotations

from datetime import date

from app.core.wrong_date_guard import parse_config_date

DEFAULT_FREE_SIGNUP_URL = "www.myfreeaiclass.com"
NONE_SCHEDULED = "(none scheduled)"

_UNSET_WORDS = {"", "upcoming", "tbd", "tba", "none", "n/a", "null"}


def is_unset(value: str | None) -> bool:
    return (value or "").strip().lower() in _UNSET_WORDS


def date_has_passed(value: str | None, today: date) -> bool:
    """True only if `value` is a full date (with year) strictly before `today`."""
    parsed = parse_config_date(value)
    if parsed is None or parsed.year is None:
        return False
    try:
        return date(parsed.year, parsed.month, parsed.day) < today
    except ValueError:
        return False


def build_schedule_block(
    class_start: str | None,
    open_house: str | None,
    rsvp_link: str | None,
    free_signup_url: str = DEFAULT_FREE_SIGNUP_URL,
) -> str:
    """
    The "what is scheduled" lines injected into every AI follow-up prompt.

    Both set  -> the two dates + RSVP (plus the exact-dates rule).
    Any unset -> an explicit OVERRIDE telling the model there is nothing to
                 date, to ignore any guideline that asks for that date, and to
                 invite the lead to start free at `free_signup_url` instead.
    """
    has_class = not is_unset(class_start)
    has_oh = not is_unset(open_house)
    lines: list[str] = []

    if has_class:
        lines.append(f"- Next class: {class_start.strip()}")  # type: ignore[union-attr]
    if has_oh:
        lines.append(f"- Next Open House: {open_house.strip()}")  # type: ignore[union-attr]
        if rsvp_link:
            lines.append(f"- Open House RSVP: {rsvp_link}")
    if has_class or has_oh:
        lines.append(
            "- Use EXACTLY the date(s) listed above; never invent or alter a class or Open House date."
        )

    missing = []
    if not has_class:
        missing.append("class start date")
    if not has_oh:
        missing.append("Open House (and its RSVP link)")
    if missing:
        lines.append(
            f"- SCHEDULE OVERRIDE: there is NO upcoming {' and no '.join(missing)} right now. "
            "Ignore any guideline below that asks you to mention it, create urgency before it, "
            "or link its RSVP. NEVER write any date for it. "
            f"Instead, invite the lead to start learning for FREE by signing up at {free_signup_url}."
        )
    return "\n".join(lines)
