"""
Unit tests for app.core.staff_roster.resolve_shift_assignee().

Covers the shift schedule this was built for:
  Balakrishna: Mon-Fri 04:30-12:30
  Farhat:      Mon-Fri 12:00-20:00
  Balamurali:  Mon-Fri 18:00-02:00; Sat 09:00-01:00(Sun)

Rules under test:
  Overlap — later-starting (incoming) shift wins.
  Gap     — soonest upcoming shift wins.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.staff_roster import resolve_shift_assignee

CHICAGO = "America/Chicago"

ROSTER = [
    {"name": "Balakrishna", "ghl_id": "BALA_ID", "days": [0, 1, 2, 3, 4],
     "shift_start": "04:30", "shift_end": "12:30"},
    {"name": "Farhat", "ghl_id": "FARHAT_ID", "days": [0, 1, 2, 3, 4],
     "shift_start": "12:00", "shift_end": "20:00"},
    {"name": "Balamurali", "ghl_id": "BALAMURALI_ID", "days": [0, 1, 2, 3, 4],
     "shift_start": "18:00", "shift_end": "02:00"},
]

ROSTER_WITH_SAT = ROSTER + [
    {"name": "Balamurali", "ghl_id": "BALAMURALI_ID", "days": [5],
     "shift_start": "09:00", "shift_end": "01:00"},
]


def _chicago(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(CHICAGO))


def test_empty_roster_returns_none():
    assert resolve_shift_assignee([], datetime.now(timezone.utc)) is None


def test_entry_missing_ghl_id_is_skipped():
    roster = [{"name": "Nobody", "ghl_id": "", "days": [0, 1, 2, 3, 4, 5, 6],
               "shift_start": "00:00", "shift_end": "23:59"}]
    assert resolve_shift_assignee(roster, datetime.now(timezone.utc)) is None


def test_weekday_morning_balakrishna():
    now = _chicago(2026, 7, 6, 8, 0)  # Monday 08:00
    assert resolve_shift_assignee(ROSTER, now) == "BALA_ID"


def test_weekday_noon_overlap_incoming_farhat_wins():
    now = _chicago(2026, 7, 6, 12, 15)  # Monday 12:15 — Balakrishna until 12:30, Farhat from 12:00
    assert resolve_shift_assignee(ROSTER, now) == "FARHAT_ID"


def test_weekday_afternoon_farhat():
    now = _chicago(2026, 7, 6, 15, 0)  # Monday 15:00
    assert resolve_shift_assignee(ROSTER, now) == "FARHAT_ID"


def test_weekday_evening_overlap_incoming_balamurali_wins():
    now = _chicago(2026, 7, 6, 19, 0)  # Monday 19:00 — Farhat until 20:00, Balamurali from 18:00
    assert resolve_shift_assignee(ROSTER, now) == "BALAMURALI_ID"


def test_weekday_late_night_balamurali():
    now = _chicago(2026, 7, 6, 23, 0)  # Monday 23:00
    assert resolve_shift_assignee(ROSTER, now) == "BALAMURALI_ID"


def test_weekday_past_midnight_still_balamurali():
    now = _chicago(2026, 7, 7, 1, 0)  # Tuesday 01:00 (still within Monday's overnight shift)
    assert resolve_shift_assignee(ROSTER, now) == "BALAMURALI_ID"


def test_weekday_overnight_gap_nearest_upcoming_balakrishna():
    now = _chicago(2026, 7, 7, 3, 0)  # Tuesday 03:00 — gap between 02:00 and 04:30
    assert resolve_shift_assignee(ROSTER, now) == "BALA_ID"


def test_saturday_predawn_gap_nearest_upcoming_is_own_saturday_shift():
    now = _chicago(2026, 7, 11, 5, 0)  # Saturday 05:00 — gap after Fri overnight ended 02:00
    assert resolve_shift_assignee(ROSTER_WITH_SAT, now) == "BALAMURALI_ID"


def test_saturday_shift_active():
    now = _chicago(2026, 7, 11, 12, 0)  # Saturday noon
    assert resolve_shift_assignee(ROSTER_WITH_SAT, now) == "BALAMURALI_ID"


def test_saturday_shift_spans_into_sunday_early_morning():
    now = _chicago(2026, 7, 12, 0, 30)  # Sunday 00:30 — still within Sat 09:00-01:00(Sun) shift
    assert resolve_shift_assignee(ROSTER_WITH_SAT, now) == "BALAMURALI_ID"


def test_sunday_gap_nearest_upcoming_monday_balakrishna():
    now = _chicago(2026, 7, 12, 10, 0)  # Sunday 10:00 — no Sunday shift; nearest upcoming is Monday 04:30
    assert resolve_shift_assignee(ROSTER_WITH_SAT, now) == "BALA_ID"


def test_naive_datetime_is_treated_as_already_in_target_timezone():
    now = datetime(2026, 7, 6, 8, 0)  # naive, Monday 08:00
    assert resolve_shift_assignee(ROSTER, now, CHICAGO) == "BALA_ID"
