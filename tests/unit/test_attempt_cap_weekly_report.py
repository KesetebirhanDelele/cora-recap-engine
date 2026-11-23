"""Unit tests for execution/attempt_cap_weekly_report.py (spec/54 monitoring). Pure logic, no database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.attempt_cap import in_holdout
from execution.attempt_cap_weekly_report import (
    Call,
    build,
    classify_cohort,
    fallback_check,
    render,
    to_phone10,
)

CUT = datetime(2026, 11, 22, 17, 10, tzinfo=timezone.utc)
ERA = datetime(2026, 8, 23, tzinfo=timezone.utc)
KW = dict(era=ERA, cap=6, guarantee=3, holdout_percent=10)


def phone(n: int) -> str:
    return f"512555{n:04d}"


def hist(p: str, n: int, *, start=ERA + timedelta(days=5)) -> list[Call]:
    return [Call(p, start + timedelta(days=i), False, False, 0.5) for i in range(n)]


def pick(want_holdout: bool) -> str:
    return next(phone(n) for n in range(2000) if in_holdout(phone(n), 10) is want_holdout)


def test_to_phone10():
    assert to_phone10("+15125550101") == "5125550101" and to_phone10("5125550101") == "5125550101"
    assert to_phone10("abc") is None and to_phone10(None) is None and to_phone10("12345") is None


def test_cohort_is_people_at_the_cap_and_split_by_holdout():
    h, c, low, old = pick(True), pick(False), phone(9001), phone(9002)
    while in_holdout(low, 10) or in_holdout(old, 10):
        low, old = phone(int(low[-4:]) + 1), phone(int(old[-4:]) + 1)
    calls = hist(h, 6) + hist(c, 7) + hist(low, 5) + hist(old, 8, start=ERA - timedelta(days=60))   # old: none since the era
    cohort = classify_cohort(calls, cutoff=CUT, **{k: v for k, v in KW.items() if k != "holdout_percent"}, holdout_percent=10)
    assert cohort == {h: "holdout", c: "capped"}        # 5 attempts is under the cap; 8 old ones are covered by the guarantee


def test_calls_after_go_live_are_counted_per_group_and_week():
    h, c = pick(True), pick(False)
    calls = hist(h, 6) + hist(c, 6)
    calls += [Call(h, CUT + timedelta(days=1), True, True, 3.0), Call(h, CUT + timedelta(days=2), False, False, 0.5),
              Call(c, CUT + timedelta(days=3), True, False, 2.0),       # a callback in the capped group: counted, not a crash
              Call(h, CUT + timedelta(days=9), True, False, 1.0)]       # week 2
    rep = build(calls, cutoff=CUT, now=CUT + timedelta(days=15), **KW)
    w1, w2, w3 = rep["weeks"]
    assert rep["cohort_people"] == {"capped": 1, "holdout": 1}
    assert (w1["holdout"]["calls"], w1["holdout"]["reached"], w1["holdout"]["booked"]) == (2, 1, 1)
    assert (w1["capped"]["calls"], w1["capped"]["reached"]) == (1, 1)
    assert w2["holdout"]["calls"] == 1 and w1["complete"] and w2["complete"] and not w3["complete"]


def test_first_hours_show_one_partial_week_and_too_early():
    now = CUT + timedelta(hours=2)
    rep = build([], cutoff=CUT, now=now, **KW)
    assert len(rep["weeks"]) == 1 and not rep["weeks"][0]["complete"] and rep["fallback"]["status"] == "TOO EARLY"
    assert "PARTIAL WEEK" in render(rep, cutoff=CUT, now=now)
    assert build([], cutoff=CUT, now=CUT, **KW)["weeks"] == []


def _week(calls, booked, reached=0):
    z = {"calls": 0, "reached": 0, "booked": 0, "minutes": 0.0}
    return {"all": {**z, "calls": calls, "booked": booked, "reached": reached}}


BASE = {"calls": 4000.0, "reached": 400.0, "booked": 40.0, "minutes": 0.0}   # four weeks totals


def test_fallback_too_early_with_one_complete_week():
    assert fallback_check(BASE, [_week(500, 9, 50)])["status"] == "TOO EARLY"


def test_fallback_ok_when_bookings_fall_with_calls():
    fb = fallback_check(BASE, [_week(500, 5, 50), _week(500, 5, 50)])      # calls -50%, bookings -50%
    assert fb["status"] == "OK" and round(fb["calls_change_pct"]) == -50 and round(fb["booked_change_pct"]) == -50


def test_fallback_flags_when_bookings_fall_much_more_than_calls():
    fb = fallback_check(BASE, [_week(800, 4, 80), _week(800, 4, 80)])      # calls -20%, bookings -60%
    assert fb["status"].startswith("REVIEW")


def test_fallback_exactly_at_threshold_is_not_flagged():
    fb = fallback_check(BASE, [_week(1000, 7, 100)] * 2)                   # calls 0%, bookings -30%
    assert fb["status"] == "OK"


def test_fallback_without_baseline():
    assert fallback_check({"calls": 0.0, "reached": 0.0, "booked": 0.0, "minutes": 0.0}, [_week(1, 1)] * 2)["status"] == "NO BASELINE"


def test_render_has_no_phone_numbers_and_marks_partial_week():
    h, c = pick(True), pick(False)
    calls = hist(h, 6) + hist(c, 6) + [Call(c, CUT + timedelta(days=8), True, True, 2.0)]
    now = CUT + timedelta(days=10)
    text = render(build(calls, cutoff=CUT, now=now, **KW), cutoff=CUT, now=now)
    assert "PARTIAL WEEK" in text and "Week 1" in text and "Switch-off check" in text
    assert h not in text and c not in text
