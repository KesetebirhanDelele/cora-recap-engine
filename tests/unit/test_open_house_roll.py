"""Open House roll-forward + retroactive-judging fix (Kes 2026-11-20). Pure logic: no database needed."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.core.schedule_context import build_schedule_block
from app.core.wrong_date_guard import find_wrong_dates
from app.services import date_expiry, wrong_date_monitor as wdm

CT_OFFSET = 6                                        # Nov is CST: UTC = Central + 6h
SET_AT = datetime(2026, 11, 20, 13, 57, tzinfo=timezone.utc)   # the dashboard save that set December 3


def _sent(day: int, hour_ct: int) -> datetime:
    return datetime(2026, 11, day, hour_ct + CT_OFFSET, 0, tzinfo=timezone.utc)


# ---- the monitor judges a message against the Open House that was next on the day it was sent ---------------------
def test_message_sent_before_the_roll_is_judged_by_its_own_day():
    # Nov 19 9:14 AM naming Nov 19: the setting later became Dec 3, but Nov 19 was the next Open House that day -> expected Nov 19
    assert wdm._open_house_expectation("December 3, 2026", SET_AT, _sent(19, 9)) == "November 19, 2026"
    assert find_wrong_dates("Join us on November 19, 2026 for the Open House", "",
                            expected_class_start=None, expected_open_house="November 19, 2026") == []


def test_a_real_mistake_is_still_flagged():
    # Nov 20 2 PM naming Nov 19 (already over): next Open House that day was Dec 3, so this is wrong
    exp = wdm._open_house_expectation("December 3, 2026", datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc), _sent(20, 14))
    # sent AFTER the setting changed -> judged against today's value
    assert exp == "December 3, 2026"
    wrong = find_wrong_dates("Join us at the Open House on November 19, 2026", "", expected_class_start=None, expected_open_house=exp)
    assert wrong


def test_skipping_ahead_is_still_flagged():
    # Dec 1: next Open House is Dec 3; a message sent Nov 25 naming Dec 17 (before the setting changed on Dec 2) is wrong
    changed = datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc)
    exp = wdm._open_house_expectation("December 17, 2026", changed, datetime(2026, 11, 25, 16, 0, tzinfo=timezone.utc))
    assert exp == "December 3, 2026"
    assert find_wrong_dates("Open House on December 17, 2026", "", expected_class_start=None, expected_open_house=exp)


def test_unset_behaviour_is_unchanged():
    assert wdm._open_house_expectation("", SET_AT, _sent(19, 9)) is None            # earlier messages were correct when sent
    assert wdm._open_house_expectation("", SET_AT, datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)) == ""


# ---- roll-forward -------------------------------------------------------------------------------------------------
def test_next_open_house_on_the_schedule():
    assert date_expiry.next_open_house(date(2026, 11, 20)) == date(2026, 12, 3)
    assert date_expiry.next_open_house(date(2026, 12, 4)) == date(2026, 12, 17)
    assert date_expiry.next_open_house(date(2026, 12, 18)) == date(2027, 1, 7)
    assert date_expiry.next_open_house(date(2027, 12, 17)) is None                  # schedule ran out -> setting is cleared instead


def test_date_text_matches_the_dashboard_format():
    assert date_expiry._long_date(date(2027, 1, 7)) == "January 7, 2027"


def test_roll_email_goes_to_kes_ali_and_jackie_once_each():
    s = SimpleNamespace(alert_email_to="kes@colaberry.com, JACKIE@colaberry.com")
    assert date_expiry.roll_recipients(s) == ["kes@colaberry.com", "JACKIE@colaberry.com", "ali@colaberry.com"]


# ---- date set but no RSVP link yet ------------------------------------------------------------------------------
def test_date_with_no_link_says_the_link_will_be_sent():
    block = build_schedule_block("", "December 17, 2026", "")
    assert "Next Open House: December 17, 2026" in block
    assert "RSVP link will be sent" in block and "Do NOT write any link" in block
    assert "Open House RSVP:" not in block


def test_date_with_link_is_unchanged():
    block = build_schedule_block("", "December 3, 2026", "https://example.com/x")
    assert "Open House RSVP: https://example.com/x" in block and "will be sent" not in block
