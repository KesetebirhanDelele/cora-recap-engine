"""
Open House reminder + "no clear date" alert - pure logic, no I/O (Kes, 2026-11-10).

One day after each Open House, Kes and Jackie get an email asking them to set up the Eventbrite page / RSVP link and the dashboard
date for the NEXT Open House. If no clear Open House date is set, they get an alert (once a day, until it is fixed).

The dates come from app.core.orientation.OPEN_HOUSES (the same list the voice prompts use), so the reminder and the prompts agree.
"""
from __future__ import annotations

from datetime import date, timedelta

from app.core.orientation import (  # noqa: F401  (list is the single source of dates)
    OPEN_HOUSE_LIST_PLACEHOLDER,
    OPEN_HOUSES,
)
from app.core.schedule_context import is_unset
from app.core.wrong_date_guard import parse_config_date

SEND_FROM_HOUR = 9               # Central: nothing goes out before 9 AM
REMINDER_WINDOW_DAYS = 7         # a reminder missed on day+1 (outage) is still sent until day+7, then dropped
JACKIE = "jackie@colaberry.com"


def long_date(d: date) -> str:
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"


def reminder_due(today: date, hour: int, open_houses: tuple[date, ...] = OPEN_HOUSES) -> date | None:
    """The Open House (a date in the list) whose reminder should go out now: its date has passed (today >= date + 1 day), it is still
    inside the window, and it is 9 AM Central or later. The most recent one wins. None if nothing is due."""
    if hour < SEND_FROM_HOUR:
        return None
    due = [d for d in open_houses if d + timedelta(days=1) <= today <= d + timedelta(days=REMINDER_WINDOW_DAYS)]
    return max(due) if due else None


def next_after(d: date, open_houses: tuple[date, ...] = OPEN_HOUSES) -> date | None:
    later = sorted(x for x in open_houses if x > d)
    return later[0] if later else None


def missing_reason(setting_value: str | None, today: date, open_houses: tuple[date, ...] = OPEN_HOUSES) -> str | None:
    """Why there is no clear Open House date right now, or None when there is one. Checks the dashboard setting (what emails use);
    also reports when the dates list has none ahead (what the voice prompts use)."""
    if not any(d >= today for d in open_houses):
        return "no upcoming Open House is in Cora's list of dates, so the voice agents are telling leads the date is not set yet"
    if is_unset(setting_value):
        return "the dashboard Settings 'Next Open House date' is blank"
    parsed = parse_config_date(setting_value)
    if parsed is None or parsed.year is None:
        return f"the dashboard Settings 'Next Open House date' ({setting_value!r}) is not a full date with a year"
    try:
        value = date(parsed.year, parsed.month, parsed.day)
    except ValueError:
        return f"the dashboard Settings 'Next Open House date' ({setting_value!r}) is not a real date"
    if value < today:
        return f"the dashboard Settings 'Next Open House date' ({setting_value}) has already passed"
    return None


def build_reminder(passed: date, next_date: date | None, remaining: list[date]) -> tuple[str, str]:
    """(subject, plain-text body). No unexplained terms; says exactly what to do."""
    if next_date:
        subject = f"Action needed: set up the {long_date(next_date)} Open House (Eventbrite link and Settings)"
        steps = [
            f"1. Make sure the Eventbrite page for the {long_date(next_date)} Open House exists, and copy its link.",
            "2. In the Cora dashboard, open Settings and put that link in the Open House RSVP link, and set the next Open House date "
            f"to {long_date(next_date)}.",
            "3. The outbound voice agents move to the new date on their own. Nothing to do there.",
            f"4. Update by hand, removing {passed.strftime('%B')} {passed.day} where it appears: the inbound voice agent in Synthflow, "
            "the AI receptionist, the GHL knowledge base, the GHL Conversation AI prompt and the GHL snippets. "
            "Dates still ahead: " + "; ".join(long_date(d) for d in remaining) + ".",
        ]
        intro = f"The Open House on {long_date(passed)} has passed. The next one in our list is {long_date(next_date)}."
    else:
        subject = "Action needed: no next Open House date is set"
        steps = [
            "1. Confirm the next Open House date with Ali.",
            "2. Make its Eventbrite page, then in the Cora dashboard Settings put in the RSVP link and the date.",
            "3. Ask Kes to add the date to Cora's list of Open House dates, so the voice agents can give it.",
            "Until then the voice agents tell leads the next date is not set yet and offer no RSVP link.",
        ]
        intro = f"The Open House on {long_date(passed)} has passed, and no later Open House date is set."
    body = intro + "\n\nWhat to do:\n" + "\n".join(steps) + "\n\nThis is an automatic reminder from Cora, sent the day after each Open House."
    return subject, body


def build_missing_alert(reason: str) -> str:
    return (f"No clear Open House date is set: {reason}. Leads are not being given a date. "
            "Set the next date (and its Eventbrite link) in dashboard Settings, and ask Kes to add it to Cora's list of dates if needed. "
            "This alert repeats once a day until it is fixed.")
