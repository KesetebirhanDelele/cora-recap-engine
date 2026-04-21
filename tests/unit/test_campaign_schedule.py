"""
Unit tests for app/core/campaign_schedule.py

Tests cover:
  is_campaign_active:
    - inside window (New Lead, any day)
    - before window opens (same day)
    - at end hour (exclusive boundary)
    - New Lead on weekend (active)
    - Cold Lead on weekend (inactive)
    - Cold Lead on Sunday (inactive)
    - Cold Lead on Friday (active)
    - case-insensitive campaign name
    - caller timezone overrides settings.default_timezone

  next_active_window_start:
    - already inside window → returns now unchanged
    - same day before window → same day at start_hour
    - past window end on active day → next active day
    - Cold Lead Friday evening → Monday at start_hour
    - Cold Lead Saturday → Monday at start_hour
    - Cold Lead Sunday → Monday at start_hour
    - New Lead Saturday past window → Sunday at start_hour
    - result is always timezone-aware
    - caller timezone used for correct local-time calculation

  get_contact_timezone:
    - returns timezone from most recent call_event
    - falls back to settings.default_timezone when no call_event exists
    - falls back when raw_payload_json has no 'timezone' key
    - falls back when timezone value is an invalid IANA name
"""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.campaign_schedule import (
    get_contact_timezone,
    is_campaign_active,
    next_active_window_start,
)
from app.models.base import Base
from app.models.call_event import CallEvent

CHICAGO = ZoneInfo("America/Chicago")
EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(weekday: int, hour: int, minute: int = 0, tz: ZoneInfo = CHICAGO) -> datetime:
    """
    Build a timezone-aware datetime anchored to an ISO weekday.
    Uses 2026-03-30 (Monday) as base — weekday 0.
    """
    from datetime import timedelta
    base = datetime(2026, 3, 30, hour, minute, tzinfo=tz)  # Monday
    return base + timedelta(days=weekday)


def _settings(**overrides):
    """Return a minimal Settings-like object with campaign window fields."""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.default_timezone = "America/Chicago"
    s.new_lead_active_days = "0,1,2,3,4,5,6"
    s.new_lead_active_start_hour = 8
    s.new_lead_active_end_hour = 22
    s.cold_lead_active_days = "0,1,2,3,4"
    s.cold_lead_active_start_hour = 8
    s.cold_lead_active_end_hour = 22
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


_event_counter = 0


def _make_call_event(
    session, contact_id: str, tz_value: str | None, created_at: datetime | None = None
) -> CallEvent:
    """Insert a minimal CallEvent with a given timezone in raw_payload_json."""
    global _event_counter
    _event_counter += 1
    payload = {"phone_number_to": "+15550001234"}
    if tz_value is not None:
        payload["timezone"] = tz_value
    from datetime import timedelta
    ts = created_at or (datetime(2026, 1, 1, 0, 0, 0) + timedelta(seconds=_event_counter))
    event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=str(uuid.uuid4()),
        contact_id=contact_id,
        status="completed",
        dedupe_key=str(uuid.uuid4()),
        raw_payload_json=payload,
        created_at=ts,
    )
    session.add(event)
    session.flush()
    return event


# ---------------------------------------------------------------------------
# is_campaign_active
# ---------------------------------------------------------------------------

def test_new_lead_active_during_window():
    """New Lead: Monday 10 AM → active."""
    now = _dt(weekday=0, hour=10)
    assert is_campaign_active("New Lead", now, _settings()) is True


def test_new_lead_inactive_before_window():
    """New Lead: Monday 7 AM → before 8 AM start → inactive."""
    now = _dt(weekday=0, hour=7)
    assert is_campaign_active("New Lead", now, _settings()) is False


def test_new_lead_inactive_at_end_hour():
    """New Lead: Monday 22:00 → end_hour is exclusive → inactive."""
    now = _dt(weekday=0, hour=22)
    assert is_campaign_active("New Lead", now, _settings()) is False


def test_new_lead_active_on_saturday():
    """New Lead: Saturday (weekday 5) inside window → active (all 7 days)."""
    now = _dt(weekday=5, hour=14)
    assert is_campaign_active("New Lead", now, _settings()) is True


def test_cold_lead_inactive_on_saturday():
    """Cold Lead: Saturday (weekday 5) → not in Mon–Fri → inactive."""
    now = _dt(weekday=5, hour=14)
    assert is_campaign_active("Cold Lead", now, _settings()) is False


def test_cold_lead_inactive_on_sunday():
    """Cold Lead: Sunday (weekday 6) → inactive."""
    now = _dt(weekday=6, hour=10)
    assert is_campaign_active("Cold Lead", now, _settings()) is False


def test_cold_lead_active_on_friday():
    """Cold Lead: Friday (weekday 4) inside window → active."""
    now = _dt(weekday=4, hour=9)
    assert is_campaign_active("Cold Lead", now, _settings()) is True


def test_case_insensitive_campaign_name():
    """Campaign name matching is case-insensitive."""
    now = _dt(weekday=5, hour=10)
    assert is_campaign_active("cold lead", now, _settings()) is False
    assert is_campaign_active("COLD_LEAD", now, _settings()) is False


def test_contact_tz_overrides_default_timezone():
    """
    A caller in America/New_York is 1 hour ahead of Chicago.
    Monday 21:30 Chicago = Monday 22:30 New York → inactive in Eastern (past 22:00).
    Without contact_tz it would be 21:30 Chicago → active.
    """
    now = _dt(weekday=0, hour=21, minute=30, tz=CHICAGO)  # 21:30 Chicago
    assert is_campaign_active("New Lead", now, _settings()) is True  # active in Chicago
    assert is_campaign_active("New Lead", now, _settings(), contact_tz="America/New_York") is False


def test_contact_tz_pacific_is_earlier():
    """
    A caller in America/Los_Angeles is 2 hours behind Chicago.
    Monday 07:00 Chicago = Monday 05:00 Pacific → inactive in Pacific (before 8).
    Without contact_tz it would be 07:00 Chicago → still inactive (before 8).
    But Monday 09:00 Chicago = 07:00 Pacific → inactive in Pacific.
    """
    now = _dt(weekday=0, hour=9, minute=0, tz=CHICAGO)  # 09:00 Chicago = 07:00 LA
    assert is_campaign_active("New Lead", now, _settings()) is True   # active in Chicago
    assert is_campaign_active("New Lead", now, _settings(), contact_tz="America/Los_Angeles") is False


# ---------------------------------------------------------------------------
# next_active_window_start
# ---------------------------------------------------------------------------

def test_already_active_returns_now():
    """If already inside window, returns the same datetime."""
    now = _dt(weekday=0, hour=10)
    result = next_active_window_start("New Lead", now, _settings())
    assert result == now


def test_same_day_before_window_returns_start_hour():
    """Monday 6 AM → returns Monday 8 AM same day."""
    now = _dt(weekday=0, hour=6)
    result = next_active_window_start("New Lead", now, _settings())
    assert result.weekday() == 0
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)
    assert result.minute == 0


def test_past_window_on_active_day_returns_next_active_day():
    """Monday 23 PM → window closed → returns Tuesday 8 AM."""
    now = _dt(weekday=0, hour=23)
    result = next_active_window_start("New Lead", now, _settings())
    assert result.weekday() == 1  # Tuesday
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)


def test_cold_lead_friday_evening_returns_monday():
    """Cold Lead: Friday 22:30 → skip Sat/Sun → Monday 8 AM."""
    now = _dt(weekday=4, hour=22, minute=30)
    result = next_active_window_start("Cold Lead", now, _settings())
    assert result.weekday() == 0  # Monday
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)
    assert result.minute == 0


def test_cold_lead_saturday_returns_monday():
    """Cold Lead: Saturday inside would-be hours → skip to Monday."""
    now = _dt(weekday=5, hour=10)
    result = next_active_window_start("Cold Lead", now, _settings())
    assert result.weekday() == 0  # Monday
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)


def test_cold_lead_sunday_returns_monday():
    """Cold Lead: Sunday → Monday 8 AM."""
    now = _dt(weekday=6, hour=15)
    result = next_active_window_start("Cold Lead", now, _settings())
    assert result.weekday() == 0
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)


def test_new_lead_saturday_past_window_returns_sunday():
    """New Lead: Saturday 23 PM → next active day is Sunday (all days active)."""
    now = _dt(weekday=5, hour=23)
    result = next_active_window_start("New Lead", now, _settings())
    assert result.weekday() == 6  # Sunday
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)


def test_result_is_timezone_aware():
    """Returned datetime must always be timezone-aware."""
    now = _dt(weekday=5, hour=23)
    result = next_active_window_start("Cold Lead", now, _settings())
    assert result.tzinfo is not None


def test_contact_tz_used_for_reschedule():
    """
    A caller in America/New_York (UTC-4 in summer / UTC-5 in winter).
    Monday 22:30 Chicago (UTC-5 winter) = Monday 23:30 Eastern.
    next_active_window_start with Eastern tz should return Tuesday 8 AM Eastern.
    Without contact_tz (Chicago) it would return Tuesday 8 AM Chicago.
    Both are Tuesday at start_hour but in different tz offsets — verify the tz
    info on the result matches the contact_tz.
    """
    now = _dt(weekday=0, hour=22, minute=30, tz=CHICAGO)
    result = next_active_window_start("New Lead", now, _settings(), contact_tz="America/New_York")
    assert result.weekday() == 1  # Tuesday
    assert result.hour == 9  # buffered 1h into window (start=8 + _WINDOW_BUFFER_HOURS=1)
    # Result should be in Eastern timezone
    eastern = ZoneInfo("America/New_York")
    assert result.tzinfo.key == eastern.key  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# get_contact_timezone
# ---------------------------------------------------------------------------

def test_get_contact_timezone_returns_from_call_event(session):
    """Returns timezone from the most recent call_event for the contact."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_call_event(session, contact_id, "America/New_York")
    result = get_contact_timezone(session, contact_id, _settings())
    assert result == "America/New_York"


def test_get_contact_timezone_returns_most_recent(session):
    """When multiple call_events exist, uses the most recent."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_call_event(session, contact_id, "America/Denver")
    _make_call_event(session, contact_id, "America/Los_Angeles")
    result = get_contact_timezone(session, contact_id, _settings())
    assert result == "America/Los_Angeles"


def test_get_contact_timezone_fallback_no_event(session):
    """Falls back to settings.default_timezone when no call_event exists."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"  # never seen before
    result = get_contact_timezone(session, contact_id, _settings(default_timezone="America/Chicago"))
    assert result == "America/Chicago"


def test_get_contact_timezone_fallback_no_tz_key(session):
    """Falls back when raw_payload_json has no 'timezone' key."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_call_event(session, contact_id, None)  # no timezone in payload
    result = get_contact_timezone(session, contact_id, _settings(default_timezone="America/Chicago"))
    assert result == "America/Chicago"


def test_get_contact_timezone_fallback_invalid_tz(session):
    """Falls back when the timezone value is not a valid IANA identifier."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_call_event(session, contact_id, "Not/A_Real_Timezone")
    result = get_contact_timezone(session, contact_id, _settings(default_timezone="America/Chicago"))
    assert result == "America/Chicago"


def test_get_contact_timezone_accepts_us_central(session):
    """Accepts legacy 'US/Central' alias which is valid IANA."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_call_event(session, contact_id, "US/Central")
    result = get_contact_timezone(session, contact_id, _settings())
    assert result == "US/Central"
