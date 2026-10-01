"""
DB-backed tests for date expiry + 24h stats + scan-after-expiry (spec/32).
Opt-in: WRONG_DATE_TEST_DATABASE_URL (throwaway DB only).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tests.unit.test_wrong_date_monitor import (  # noqa: F401  (fixtures)
    DB_URL,
    NOW,
    _add_msg,
    _incident_ids,
    _scan,
    client,
    session,
    settings,
)

pytestmark = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")

from app.services import date_expiry, wrong_date_monitor as wdm  # noqa: E402

CHICAGO_NOV13_NOON = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)   # 12:00 CST, Nov 13


def _cfg(session, key):
    return session.execute(text("SELECT value FROM app_config WHERE key=:k"), {"k": key}).scalar()


def _set(session, key, value):
    session.execute(text("""
        INSERT INTO app_config (key, value, updated_by) VALUES (:k, :v, 'test')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
    """), {"k": key, "v": value})
    session.commit()


def _expire(session, settings, now):
    with patch("app.services.alerting._send_alert_email") as email:
        cleared = date_expiry.expire_past_dates(session, settings, now)
        session.commit()
    return cleared, email


# ── expiry ───────────────────────────────────────────────────────────────────

def test_clears_only_dates_that_have_passed(session, settings):
    # class Nov 12, 2026 (passed on Nov 13) ; open house Dec 3, 2026 (future)
    _set(session, "next_open_house_date", "December 3, 2026")
    session.execute(text("DELETE FROM audit_log WHERE action='config_expired'"))
    session.commit()
    cleared, email = _expire(session, settings, CHICAGO_NOV13_NOON)
    assert cleared == ["next_class_start"]
    assert _cfg(session, "next_class_start") == ""
    assert _cfg(session, "next_open_house_date") == "December 3, 2026"
    assert email.call_count == 1
    assert email.call_args.kwargs["alert_type"] == "date_setting_expired"
    assert "November 12, 2026" in email.call_args.kwargs["message"]
    assert "www.myfreeaiclass.com" in email.call_args.kwargs["message"]
    assert session.execute(text(
        "SELECT COUNT(*) FROM audit_log WHERE action='config_expired' AND entity_id='next_class_start'")).scalar() == 1
    assert session.execute(text(
        "SELECT updated_by FROM app_config WHERE key='next_class_start'")).scalar() == "auto_expire"


def test_not_cleared_on_the_day_itself(session, settings):
    _set(session, "next_open_house_date", "December 3, 2026")
    on_day = datetime(2026, 11, 12, 23, 0, tzinfo=timezone.utc)      # 5pm CST Nov 12
    cleared, _ = _expire(session, settings, on_day)
    assert cleared == []
    assert _cfg(session, "next_class_start") == "November 12, 2026"


def test_uses_configured_timezone_for_the_day_boundary(session, settings):
    _set(session, "next_open_house_date", "December 3, 2026")
    # 03:00 UTC Nov 13 is still Nov 12 in Chicago -> not expired yet
    before_chicago_midnight = datetime(2026, 11, 13, 3, 0, tzinfo=timezone.utc)
    assert _expire(session, settings, before_chicago_midnight)[0] == []
    # 07:00 UTC Nov 13 = 01:00 CST Nov 13 -> expired
    assert _expire(session, settings, datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc))[0] == ["next_class_start"]


def test_expiry_is_idempotent(session, settings):
    _expire(session, settings, CHICAGO_NOV13_NOON)
    cleared, email = _expire(session, settings, CHICAGO_NOV13_NOON + timedelta(minutes=1))
    assert cleared == [] and email.call_count == 0


def test_yearless_value_is_never_auto_cleared(session, settings):
    _set(session, "next_class_start", "Nov 12")
    cleared, _ = _expire(session, settings, CHICAGO_NOV13_NOON + timedelta(days=200))
    assert "next_class_start" not in cleared
    assert _cfg(session, "next_class_start") == "Nov 12"


def test_operator_edit_between_read_and_clear_is_not_overwritten(session, settings):
    # value changes to a future date after get_str would have read the old one
    from app.core import app_config

    real = app_config.get_str

    def stale_read(key, sess, stg, fb=""):
        v = real(key, sess, stg, fb)
        if key == "next_class_start":
            _set(sess, "next_class_start", "December 20, 2026")   # operator saves a new date
            return "November 12, 2026"                              # ...but we judged the old one
        return v

    with patch("app.core.app_config.get_str", stale_read):
        cleared, _ = _expire(session, settings, CHICAGO_NOV13_NOON)
    assert "next_class_start" not in cleared
    assert _cfg(session, "next_class_start") == "December 20, 2026"


# ── correction text when nothing is scheduled ────────────────────────────────

def test_correction_text_when_nothing_scheduled(session, settings):
    _set(session, "next_class_start", "")
    _set(session, "next_open_house_date", "")
    txt = wdm.build_correction_text(session, settings)
    assert "don't have a class or Open House scheduled" in txt
    assert "www.myfreeaiclass.com" in txt
    assert "RSVP" not in txt and "2026" not in txt


def test_correction_text_when_only_open_house_expired(session, settings):
    _set(session, "next_open_house_date", "")
    txt = wdm.build_correction_text(session, settings)
    assert "November 12, 2026" in txt and "no Open House scheduled" in txt
    assert "RSVP" not in txt and "www.myfreeaiclass.com" in txt


# ── scan after expiry: only messages sent AFTER the clear are wrong ──────────

def test_scan_after_expiry_ignores_messages_sent_before_the_clear(session, settings):
    # message told the (then-correct) class date, sent yesterday; date cleared just now
    _add_msg(session, "Hi! Our next class starts November 12, 2026.", contact="before", age_h=3)
    _set(session, "next_class_start", "")
    _add_msg(session, "Hi! Our next class starts November 12, 2026.", contact="after", age_h=-0.01)
    n, _ = _scan(session, settings)
    rows = session.execute(text("SELECT contact_id FROM wrong_date_incidents")).fetchall()
    assert n == 1 and rows == [("after",)]


def test_scan_after_expiry_does_not_flag_free_signup_message(session, settings):
    _set(session, "next_class_start", "")
    _set(session, "next_open_house_date", "")
    _add_msg(session, "Start learning for free at www.myfreeaiclass.com. Text STOP to stop alerts",
             age_h=-0.01)
    n, _ = _scan(session, settings)
    assert n == 0


def test_alert_message_says_nothing_scheduled(session, settings):
    _set(session, "next_class_start", "")
    _add_msg(session, "Our next class starts May 30, 2026.", age_h=-0.01)
    _, email = _scan(session, settings)
    assert "none - nothing is scheduled" in email.call_args.kwargs["message"]


# ── stats ────────────────────────────────────────────────────────────────────

def test_incident_stats_open_and_closed_24h(session, settings):
    for c in ("a", "b", "c", "d"):
        _add_msg(session, "Our next class starts May 30, 2026.", contact=c)
    _scan(session, settings)
    ids = {r[0]: r[1] for r in session.execute(text("SELECT contact_id, id FROM wrong_date_incidents")).fetchall()}
    wdm.dismiss_incident(session, ids["a"], "kes")
    session.execute(text("UPDATE wrong_date_incidents SET status='corrected', resolved_at=now() WHERE id=:i"), {"i": ids["b"]})
    # closed 2 days ago -> must not count
    session.execute(text("UPDATE wrong_date_incidents SET status='corrected', resolved_at=now() - interval '2 days' WHERE id=:i"), {"i": ids["c"]})
    session.commit()
    s = wdm.incident_stats(session)
    assert s == {"open": 1, "closed_24h": 2, "corrected_24h": 1, "dismissed_24h": 1, "new_24h": 4}


def test_route_returns_stats(session, settings, client):
    _add_msg(session, "Our next class starts May 30, 2026.")
    _scan(session, settings)
    body = client.get("/dashboard/wrong-dates").json()
    assert body["stats"]["open"] == 1 and body["stats"]["closed_24h"] == 0
