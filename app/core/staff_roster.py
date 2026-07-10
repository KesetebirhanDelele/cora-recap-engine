"""
Staff roster shift resolution.

Resolves which roster entry (a {name, ghl_id, days, shift_start, shift_end}
dict) is "on shift" at a given moment, given a weekly recurring schedule.

Rules (per product decision):
  Overlap  — when two shifts are both active at once, the one that started
             most recently wins (the incoming shift takes over).
  Gap      — when no shift is active, the entry whose shift starts soonest
             (wrapping into next week if needed) wins.

days: list of ints, 0=Monday .. 6=Sunday (matches DayPicker convention in
SettingsClient.tsx and Python's datetime.weekday()).
shift_start / shift_end: "HH:MM" 24-hour strings. shift_end <= shift_start
is interpreted as an overnight shift crossing into the next day.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY


def _parse_hhmm(value: str) -> int:
    hh, mm = value.strip().split(":")
    return int(hh) * 60 + int(mm)


def _week_minute(local_now: datetime) -> int:
    return local_now.weekday() * MINUTES_PER_DAY + local_now.hour * 60 + local_now.minute


def _expand_intervals(entry: dict[str, Any]) -> list[tuple[int, int]]:
    start_min = _parse_hhmm(entry["shift_start"])
    end_min = _parse_hhmm(entry["shift_end"])
    intervals = []
    for day in entry.get("days", []):
        start = int(day) * MINUTES_PER_DAY + start_min
        end = int(day) * MINUTES_PER_DAY + end_min
        if end <= start:
            end += MINUTES_PER_DAY
        intervals.append((start, end))
    return intervals


def resolve_shift_assignee(
    roster: list[dict[str, Any]],
    now: datetime,
    timezone: str = "America/Chicago",
) -> str | None:
    """
    Return the ghl_id of whoever is on shift at `now`, or None if the
    roster is empty / has no usable entries.
    """
    if not roster:
        return None

    tz = ZoneInfo(timezone)
    local_now = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    now_minute = _week_minute(local_now)

    active: list[tuple[int, str]] = []
    upcoming: list[tuple[int, str]] = []

    for entry in roster:
        ghl_id = str(entry.get("ghl_id") or "").strip()
        if not ghl_id:
            continue
        for start, end in _expand_intervals(entry):
            for offset in (-MINUTES_PER_WEEK, 0, MINUTES_PER_WEEK):
                s, e = start + offset, end + offset
                if s <= now_minute < e:
                    active.append((s, ghl_id))
                elif s > now_minute:
                    upcoming.append((s - now_minute, ghl_id))

    if active:
        active.sort(key=lambda pair: pair[0])
        return active[-1][1]  # latest-starting active shift = incoming

    if upcoming:
        upcoming.sort(key=lambda pair: pair[0])
        return upcoming[0][1]  # soonest upcoming shift

    return None
