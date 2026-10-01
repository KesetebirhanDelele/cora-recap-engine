"""Pre-send SMS gate (spec/34): segment counting, Pacific-day budget (max 999), pacing, content rules,
next-day deferral at a legal hour, and the ledger that enforces it. Pure + sqlite - no Postgres needed."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core import sms_gate as g

UTC = timezone.utc
OK_TEXT = "Hi, it's Cora from Colaberry. Reply YES for details. Text STOP to stop alerts"


# ── segments ──
def test_segment_counts():
    assert g.count_segments("") == 0
    assert g.count_segments("a" * 160) == 1
    assert g.count_segments("a" * 161) == 2           # concatenated parts carry 153 each
    assert g.count_segments("a" * 306) == 2
    assert g.count_segments("a" * 307) == 3
    assert g.count_segments("€" * 80) == 1            # € is a GSM extension char = 2 septets
    assert g.count_segments("€" * 81) == 2
    assert g.count_segments("Hi 😀") == 1             # UCS-2: 70 units
    assert g.count_segments("😀" * 36) == 2           # 72 UTF-16 units > 70 -> 67-unit parts


# ── Pacific day ──
def test_pacific_day_bounds_and_dst():
    now = datetime(2026, 10, 1, 3, 30, tzinfo=UTC)               # 20:30 PDT on Sep 30
    assert g.pacific_day_start_utc(now) == datetime(2026, 9, 30, 7, 0, tzinfo=UTC)
    assert g.next_pacific_day_start_utc(now) == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
    winter = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    assert g.next_pacific_day_start_utc(winter) == datetime(2026, 12, 2, 8, 0, tzinfo=UTC)
    fall_back = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)         # 25-hour day
    assert g.next_pacific_day_start_utc(fall_back) == datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


def test_next_legal_send_time_moves_into_tcpa_hours_in_the_leads_zone():
    midnight_pt = datetime(2026, 10, 2, 7, 5, tzinfo=UTC)         # 00:05 Pacific = 02:05 Central
    got = g.next_legal_send_time(midnight_pt, "America/Chicago")
    assert got == datetime(2026, 10, 2, 13, 5, tzinfo=UTC)        # 08:05 Central
    noon = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)               # 12:00 Central: already legal
    assert g.next_legal_send_time(noon, "America/Chicago") == noon
    late = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)                # 22:00 Central Oct 1
    assert g.next_legal_send_time(late, "America/Chicago") == datetime(2026, 10, 2, 13, 5, tzinfo=UTC)
    assert g.next_legal_send_time(midnight_pt, "Not/AZone") == datetime(2026, 10, 2, 13, 5, tzinfo=UTC)


# ── decide ──
CFG = g.GateConfig()


def test_hard_cap_never_exceeds_999_even_if_configured_higher():
    assert g.GateConfig(daily_cap=5000).effective_daily_cap == 999
    assert g.GateConfig(daily_cap=100).effective_daily_cap == 100
    assert g.GateConfig(daily_cap=-5).effective_daily_cap == 0


def test_allows_a_clean_text_with_budget():
    d = g.decide(OK_TEXT, g.GateUsage(10, 600, 0), CFG)
    assert d.action == g.ALLOW and d.segments == 1


def test_defers_to_next_day_when_budget_would_be_exceeded():
    assert g.decide(OK_TEXT, g.GateUsage(998, None, 0), CFG).action == g.ALLOW      # 998 + 1 = 999
    d = g.decide(OK_TEXT, g.GateUsage(999, None, 0), CFG)
    assert d.action == g.DEFER and d.code == "daily_cap" and d.next_day
    two = "word " * 40                                                                # 200 chars = 2 segments
    assert g.decide(two, g.GateUsage(998, None, 0), CFG).code == "daily_cap"          # 998 + 2 > 999


def test_pacing_min_gap_and_per_minute():
    d = g.decide(OK_TEXT, g.GateUsage(0, 2.0, 1), CFG)
    assert d.action == g.DEFER and d.code == "min_gap" and 3 <= d.retry_after_seconds <= 4
    d = g.decide(OK_TEXT, g.GateUsage(0, 30.0, 12), CFG)
    assert d.action == g.DEFER and d.code == "per_minute"
    assert g.decide(OK_TEXT, g.GateUsage(0, 5.0, 11), CFG).action == g.ALLOW


@pytest.mark.parametrize("body,why", [
    ("", "empty"),
    ("warm_lead", "stray token"),
    ("Visit myfreeaiclass.com to start learning for free today", "link"),
    ("RSVP https://eventbrite.com/e/1 for the open house today", "link"),
    ("Hi {{contact.first_name}} it is Cora from Colaberry", "placeholder"),
    ("Hi None it is Cora from Colaberry today", "placeholder"),
    ("word " * 400, "too long"),
])
def test_content_rules_block(body, why):
    d = g.decide(body, g.GateUsage(), CFG)
    assert d.action == g.BLOCK, why


def test_wrong_date_in_an_outgoing_sms_is_blocked_and_correct_one_passes():
    cfg = g.GateConfig(expected_class_start="November 12, 2026", expected_open_house="October 29, 2026")
    bad = "Our next class starts on December 3, 2026. Reply YES to join"
    good = "Our next class starts on November 12, 2026. Reply YES to join"
    assert g.decide(bad, g.GateUsage(), cfg).action == g.BLOCK
    assert g.decide(good, g.GateUsage(), cfg).action == g.ALLOW
    expired = g.GateConfig(expected_class_start="", expected_open_house="")             # dates reset to blank
    assert g.decide(good, g.GateUsage(), expired).action == g.BLOCK


def test_domain_can_be_re_allowed_by_config():
    cfg = g.GateConfig(allowed_link_domains="myfreeaiclass.com")
    assert g.decide("Start free at myfreeaiclass.com whenever you like", g.GateUsage(), cfg).action == g.ALLOW


def test_usage_level():
    assert g.usage_level(100, 999) == "ok"
    assert g.usage_level(800, 999) == "warning"
    assert g.usage_level(999, 999) == "exhausted"


# ── ledger (sqlite) ──
@pytest.fixture()
def ledger(monkeypatch):
    from app.services import sms_ledger as L

    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE sms_send_ledger (id TEXT PRIMARY KEY, contact_id TEXT, source TEXT,
            status TEXT, code TEXT, reason TEXT, segments INTEGER, body TEXT, pacific_day DATE,
            created_at TIMESTAMP)"""))
    monkeypatch.setattr(L, "_config", lambda s, st: g.GateConfig(daily_cap=5))
    alerts = []
    monkeypatch.setattr(L, "_alert_once_per_day", lambda *a, **k: alerts.append(a[2]))

    @contextmanager
    def factory():
        with Session(engine) as s:
            yield s

    def run(body=OK_TEXT, now=None, source="followup"):
        return L.reserve(NS(), contact_id="c1", body=body, source=source, now=now, session_factory=factory)

    return NS(L=L, run=run, factory=factory, alerts=alerts, engine=engine)


def _t(i):  # spaced > min gap apart, same Pacific day (Oct 1 2026 noon UTC = 05:00 PT)
    return datetime(2026, 10, 1, 12, 0, tzinfo=UTC) + timedelta(seconds=10 * i)


def test_ledger_spends_budget_then_defers_to_next_day(ledger):
    for i in range(5):
        r = ledger.run(now=_t(i))
        assert r.allowed and r.ledger_id
    r = ledger.run(now=_t(5))
    assert not r.allowed and r.code == "daily_cap" and r.defer_until is not None
    # next Pacific midnight + 5 min, moved to 08:05 Central the same morning (13:05 UTC)
    assert r.defer_until == datetime(2026, 10, 2, 13, 5, tzinfo=UTC)
    assert "exhausted" in ledger.alerts


def test_failed_write_releases_the_budget_and_sent_keeps_it(ledger):
    ids = [ledger.run(now=_t(i)).ledger_id for i in range(5)]
    ledger.L.mark_failed(ids[0], "GHL write failed", ledger.factory)
    ledger.L.mark_sent(ids[1], ledger.factory)
    assert ledger.run(now=_t(6)).allowed                    # one segment was released
    assert not ledger.run(now=_t(7)).allowed                # full again


def test_new_pacific_day_starts_a_fresh_budget(ledger):
    for i in range(5):
        ledger.run(now=_t(i))
    assert not ledger.run(now=_t(6)).allowed
    assert ledger.run(now=datetime(2026, 10, 2, 14, 0, tzinfo=UTC)).allowed


def test_pacing_defers_without_spending_or_logging(ledger):
    assert ledger.run(now=_t(0)).allowed
    r = ledger.run(now=_t(0) + timedelta(seconds=1))
    assert not r.allowed and r.code == "min_gap" and r.defer_until > _t(0) + timedelta(seconds=1)
    with ledger.factory() as s:
        n = s.execute(text("SELECT COUNT(*) FROM sms_send_ledger")).scalar()
    assert n == 1


def test_blocked_text_is_logged_and_never_spends_budget(ledger):
    r = ledger.run(body="see myfreeaiclass.com now please", now=_t(0))
    assert not r.allowed and r.defer_until is None
    with ledger.factory() as s:
        row = s.execute(text("SELECT status, segments FROM sms_send_ledger")).fetchone()
        used = ledger.L.usage(s, _t(1)).segments_today
    assert row[0] == "blocked" and used == 0


def test_unknown_source_is_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.run(source="mystery")
