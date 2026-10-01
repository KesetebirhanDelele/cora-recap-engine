"""Unit tests for app/core/wrong_date_guard.py — pure logic, no I/O (spec/32)."""
from __future__ import annotations

import pytest

from app.core.wrong_date_guard import (
    KIND_APPOINTMENT,
    KIND_CLASS,
    KIND_OPEN_HOUSE,
    extract_date_mentions,
    find_wrong_dates,
    parse_config_date,
)

CLASS = "November 12, 2026"
OH = "October 29, 2026"


def _check(body, subject=None, cls=CLASS, oh=OH):
    return find_wrong_dates(body, subject, expected_class_start=cls, expected_open_house=oh)


# ── parse_config_date ─────────────────────────────────────────────────────────

def test_parse_config_full_date():
    p = parse_config_date("November 12, 2026")
    assert (p.month, p.day, p.year) == (11, 12, 2026)


@pytest.mark.parametrize("value", ["upcoming", "Q3 2026", "", None, "soon"])
def test_parse_config_unparseable_is_none(value):
    assert parse_config_date(value) is None


# ── happy path: correct dates never alert ────────────────────────────────────

def test_correct_dates_no_alert():
    body = "Our next class starts November 12, 2026 and the next Open House is October 29, 2026."
    assert _check(body) == []


def test_correct_dates_abbreviated_and_ordinal_no_alert():
    body = "Next class begins Nov 12th. Join our open house on Oct. 29!"
    assert _check(body) == []


def test_numeric_format_correct_no_alert():
    assert _check("Open house is 10/29 and class starts 11/12.") == []


def test_message_without_dates_no_alert():
    assert _check("Hi Sam, just checking in — any questions about the program?") == []


# ── wrong dates ──────────────────────────────────────────────────────────────

def test_stale_class_date_alerts():
    wrong = _check("Our next class starts May 30, 2026 — seats are filling up.")
    assert len(wrong) == 1
    assert wrong[0].kind == KIND_CLASS
    assert wrong[0].raw == "May 30, 2026"
    assert wrong[0].expected == CLASS


def test_wrong_open_house_date_alerts():
    wrong = _check("Join our Open House on November 5 to learn more.")
    assert [w.kind for w in wrong] == [KIND_OPEN_HOUSE]
    assert wrong[0].expected == OH


def test_both_wrong_in_one_message():
    wrong = _check("Class starts Dec 1. Open house is Nov 3.")
    assert sorted(w.kind for w in wrong) == [KIND_CLASS, KIND_OPEN_HOUSE]


def test_class_date_equal_to_open_house_date_alerts_swap():
    wrong = _check("Our next class starts October 29, 2026.")
    assert [w.kind for w in wrong] == [KIND_CLASS]


def test_year_mismatch_alerts():
    wrong = _check("Next class starts November 12, 2025.")
    assert [w.kind for w in wrong] == [KIND_CLASS]


def test_year_omitted_matches_on_month_and_day():
    assert _check("Next class starts November 12.") == []


def test_numeric_wrong_alerts():
    wrong = _check("Open house 10/30 — RSVP today!")
    assert [w.kind for w in wrong] == [KIND_OPEN_HOUSE]


def test_day_first_format_detected():
    wrong = _check("The next cohort starts on 5 December 2026.")
    assert [w.kind for w in wrong] == [KIND_CLASS]


def test_wrong_date_in_email_subject_and_html_body():
    wrong = _check(
        "<p>Hi Sam,</p><p>Reserve your seat — the <b>Open&nbsp;House</b> is on <b>Nov 3</b>.</p>",
        subject="Open house this week",
    )
    assert [w.kind for w in wrong] == [KIND_OPEN_HOUSE]


# ── appointment / call-back dates are NOT class or open-house dates ──────────

def test_call_you_date_is_ignored():
    assert _check("We'll call you on Oct 6 to chat about the program.") == []


def test_appointment_date_is_ignored():
    assert _check("Your appointment is confirmed for Tuesday, September 30.") == []


def test_callback_date_next_to_class_word_still_ignored():
    # "call you" is closer to the date than "class" → appointment
    assert _check("I will call you on Oct 6 about the next class.") == []


def test_mixed_sentence_correct_class_plus_appointment_no_alert():
    body = "Our next class starts November 12, 2026. We'll call you on Oct 6 to finish enrollment."
    assert _check(body) == []


def test_wrong_class_date_still_caught_next_to_appointment_date():
    body = "We'll call you on Oct 6. Our next class starts Dec 1."
    wrong = _check(body)
    assert [(w.kind, w.raw) for w in wrong] == [(KIND_CLASS, "Dec 1")]


def test_date_with_no_context_is_ignored():
    assert _check("Your birthday is on March 3.") == []


def test_mentions_are_classified():
    kinds = {
        m.raw: m.kind
        for m in extract_date_mentions(
            "We'll call you on Oct 6. Our next class starts Nov 12. Open house is Oct 29."
        )
    }
    assert kinds == {
        "Oct 6": KIND_APPOINTMENT,
        "Nov 12": KIND_CLASS,
        "Oct 29": KIND_OPEN_HOUSE,
    }


# ── config edge cases ────────────────────────────────────────────────────────

def test_unparseable_class_config_skips_class_check_only():
    wrong = find_wrong_dates(
        "Class starts Dec 1. Open house is Nov 3.", None,
        expected_class_start="upcoming", expected_open_house=OH,
    )
    assert [w.kind for w in wrong] == [KIND_OPEN_HOUSE]


def test_checks_disabled_with_none_never_alerts():
    assert find_wrong_dates("Class starts Dec 1.", None,
                            expected_class_start=None, expected_open_house=None) == []


def test_blank_means_nothing_scheduled_so_any_dated_mention_is_wrong():
    wrong = find_wrong_dates(
        "Our next class starts Nov 12. Open house is Oct 29. We'll call you Oct 6.", None,
        expected_class_start="", expected_open_house="",
    )
    assert sorted((w.kind, w.raw, w.expected) for w in wrong) == [
        (KIND_CLASS, "Nov 12", ""),
        (KIND_OPEN_HOUSE, "Oct 29", ""),
    ]  # appointment date still ignored


def test_blank_with_no_dates_is_fine():
    assert find_wrong_dates(
        "Start learning for free at www.myfreeaiclass.com today!", None,
        expected_class_start="", expected_open_house="",
    ) == []


def test_none_body_is_safe():
    assert find_wrong_dates(None, None, expected_class_start=CLASS, expected_open_house=OH) == []


def test_invalid_calendar_date_ignored():
    assert _check("Class starts Feb 31.") == []
