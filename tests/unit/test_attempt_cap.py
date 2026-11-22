"""Unit tests for app.core.attempt_cap (spec/54): at most 6 lifetime AI attempts per person, with an AI-class
guarantee of 3 and a 10% uncapped holdout."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.attempt_cap import (
    check_attempt_cap,
    count_attempts,
    decide,
    in_holdout,
    phone_variants,
)
from app.models.base import Base
from app.models.call_event import CallEvent

ERA = datetime(2026, 8, 23, tzinfo=timezone.utc)
PHONE = "+15125550101"


def _settings(**over):
    base = dict(outbound_attempt_cap_enabled=True, outbound_attempt_cap=6, outbound_attempt_cap_era_start="2026-08-23",
                outbound_attempt_cap_era_guarantee=3, outbound_attempt_cap_holdout_percent=0)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def session():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s
    eng.dispose()


def _call(session, *, phone=PHONE, when=ERA + timedelta(days=10), direction="outbound", agent="ColdLead", excluded=False):
    session.add(CallEvent(id=str(uuid.uuid4()), call_id=str(uuid.uuid4()), dedupe_key=str(uuid.uuid4()),
                          contact_id=phone, direction=direction, voice_agent=agent, call_started_at=when,
                          report_excluded=excluded))
    session.flush()


# ---- pure rule -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lifetime,since,holdout,blocked,reason", [
    (0, 0, False, False, "allowed_under_cap"),
    (5, 5, False, False, "allowed_under_cap"),          # one below the cap: still allowed
    (6, 6, False, True, "blocked_at_cap"),              # at the cap with plenty of AI-class calls: blocked
    (6, 3, False, True, "blocked_at_cap"),              # exactly the guarantee already used: blocked
    (6, 2, False, False, "allowed_ai_guarantee"),       # old leads who have had < 3 AI-class calls still hear the pitch
    (12, 0, False, False, "allowed_ai_guarantee"),      # dialed 12 times under the old pitch, none since 23 Aug
    (12, 3, False, True, "blocked_at_cap"),
    (40, 40, True, False, "allowed_holdout"),           # holdout is never capped
])
def test_decision_table(lifetime, since, holdout, blocked, reason):
    d = decide(lifetime, since, cap=6, guarantee=3, holdout=holdout)
    assert (d.blocked, d.reason) == (blocked, reason)


# ---- holdout ---------------------------------------------------------------------------------------------------

def test_holdout_is_deterministic_and_close_to_the_percentage():
    phones = [f"+1512555{n:04d}" for n in range(2000)]
    first = [in_holdout(p, 10) for p in phones]
    assert first == [in_holdout(p, 10) for p in phones]          # same person, same answer, every time
    assert 120 <= sum(first) <= 280                              # about 10% of 2,000 (loose bounds, no flakiness)
    assert in_holdout("+15125550101", 10) == in_holdout("5125550101", 10) == in_holdout("1 (512) 555-0101", 10)


def test_holdout_zero_or_bad_numbers_are_never_in_the_holdout():
    assert not in_holdout(PHONE, 0) and not in_holdout("123", 50) and not in_holdout("", 50)
    assert in_holdout(PHONE, 100)


def test_phone_variants():
    assert phone_variants("(512) 555-0101") == ["+15125550101", "15125550101", "5125550101"]
    assert phone_variants("+15125550101") == ["+15125550101", "15125550101", "5125550101"]
    assert phone_variants("abc") == [] and phone_variants("") == []


# ---- counting --------------------------------------------------------------------------------------------------

def test_count_splits_lifetime_and_since_era_and_ignores_other_calls(session):
    for d in (30, 20):                                               # two old-pitch attempts
        _call(session, when=ERA - timedelta(days=d))
    for d in (1, 2, 3):                                              # three AI-class attempts
        _call(session, when=ERA + timedelta(days=d), agent="NewLead")
    _call(session, direction="inbound")                              # inbound does not count
    _call(session, agent="Inbound")                                  # other agent does not count
    _call(session, excluded=True)                                    # excluded rows do not count
    _call(session, phone="+15125550999")                             # someone else
    assert count_attempts(session, PHONE, ERA) == (5, 3)
    assert count_attempts(session, "512-555-0101", ERA) == (5, 3)    # any spelling of the number
    assert count_attempts(session, "", ERA) == (0, 0)


# ---- the check -------------------------------------------------------------------------------------------------

def test_person_at_six_with_ai_class_calls_is_blocked(session):
    for d in range(6):
        _call(session, when=ERA + timedelta(days=d + 1))
    r = check_attempt_cap(session, PHONE, _settings())
    assert r == {"lifetime": 6, "since_era": 6, "cap": 6}


def test_person_at_five_is_allowed(session):
    for d in range(5):
        _call(session, when=ERA + timedelta(days=d + 1))
    assert check_attempt_cap(session, PHONE, _settings()) is None


def test_old_lead_with_many_old_calls_and_none_since_is_allowed_by_the_guarantee(session):
    for d in range(10):
        _call(session, when=ERA - timedelta(days=30 + d))
    assert check_attempt_cap(session, PHONE, _settings()) is None
    for d in range(3):                                               # after 3 AI-class calls the guarantee is used up
        _call(session, when=ERA + timedelta(days=d + 1))
    assert check_attempt_cap(session, PHONE, _settings())["lifetime"] == 13


def test_holdout_person_is_never_blocked(session):
    for d in range(10):
        _call(session, when=ERA + timedelta(days=d + 1))
    assert check_attempt_cap(session, PHONE, _settings(outbound_attempt_cap_holdout_percent=100)) is None


def test_switch_off_or_unusable_settings_or_no_phone_means_no_lookup(session):
    for d in range(8):
        _call(session, when=ERA + timedelta(days=d + 1))
    assert check_attempt_cap(session, PHONE, _settings(outbound_attempt_cap_enabled=False)) is None
    assert check_attempt_cap(session, PHONE, _settings(outbound_attempt_cap_era_start="not a date")) is None
    assert check_attempt_cap(session, PHONE, SimpleNamespace()) is None
    assert check_attempt_cap(session, "", _settings()) is None


def test_a_database_error_fails_open():
    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    assert check_attempt_cap(Boom(), PHONE, _settings()) is None


def test_real_settings_defaults():
    from app.config.settings import Settings

    f = Settings.model_fields
    assert f["outbound_attempt_cap_enabled"].default is True
    assert f["outbound_attempt_cap"].default == 6
    assert f["outbound_attempt_cap_era_start"].default == "2026-08-23"
    assert f["outbound_attempt_cap_era_guarantee"].default == 3
    assert f["outbound_attempt_cap_holdout_percent"].default == 10
