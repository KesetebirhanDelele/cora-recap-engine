"""
Orientation dates for the voice prompts (Ali, 2026-11-10) - pure logic, no I/O.

Since 2026-11-05 there is NO class start date: enrollment is self-paced and runs in monthly batches, a new student starts as soon
as they enroll, and the only dated event is orientation - the first Monday of the month after they join, 6:30 to 8:00 PM Central.
Prompts must always have a date to give, so it is computed here and never hardcoded: "the next orientation on the calendar" is the
earliest of the first Monday of each month and any one-off orientation, on or after today (Central time).

The prompt docs hold the placeholder {{NEXT_ORIENTATION}}; render_placeholders() fills it every time a prompt is sent.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = "America/Chicago"
MONTHLY_TIME = "6:30 to 8:00 PM Central"
# One-off orientations on top of the monthly pattern. Add a date here (no code change elsewhere) when Ali announces one.
# 2026-11-12 (Thursday): Ali 2026-11-10 - "the date leads need to hear right now"; no time was given, so none is spoken.
ONE_OFF: tuple[date, ...] = (date(2026, 11, 12),)

PLACEHOLDER = "{{NEXT_ORIENTATION}}"


def first_monday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(0 - d.weekday()) % 7)


def central_today(now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo(TIMEZONE))).astimezone(ZoneInfo(TIMEZONE)).date()


def next_orientation(today: date) -> tuple[date, bool]:
    """(date, is_monthly) of the next orientation on or after `today`. is_monthly is False for a one-off (no time known)."""
    candidates: list[tuple[date, bool]] = [(d, False) for d in ONE_OFF if d >= today]
    y, m = today.year, today.month
    for _ in range(14):                                  # far more than needed: there is one every month
        fm = first_monday(y, m)
        if fm >= today:
            candidates.append((fm, True))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return min(candidates, key=lambda c: c[0])


def orientation_text(today: date) -> str:
    d, monthly = next_orientation(today)
    label = f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}"
    return f"{label}, {MONTHLY_TIME}" if monthly else label


def upcoming_texts(today: date, count: int = 2) -> list[str]:
    """The next `count` orientations on the calendar as text (a one-off first, then the monthly Mondays)."""
    out: list[str] = []
    d = today
    for _ in range(count):
        nxt, monthly = next_orientation(d)
        out.append(orientation_text(nxt))
        d = nxt + timedelta(days=1)
    return out


def render_placeholders(text: str, now: datetime | None = None) -> str:
    """Fill {{NEXT_ORIENTATION}} with today's next orientation (Central time). Text without the placeholder is returned unchanged."""
    if PLACEHOLDER not in text:
        return text
    return text.replace(PLACEHOLDER, orientation_text(central_today(now)))
