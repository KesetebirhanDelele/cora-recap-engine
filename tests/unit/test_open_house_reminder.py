"""Open House reminder (day after each Open House) and the missing-date alert (Kes, 2026-11-10)."""
from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core import open_house_reminder as r
from app.services import open_house_reminder as svc

NOV19, DEC3, DEC17 = date(2026, 11, 19), date(2026, 12, 3), date(2026, 12, 17)


@pytest.mark.parametrize("today,hour,want", [
    (date(2026, 11, 19), 10, None),            # the day itself: not yet
    (date(2026, 11, 20), 8, None),             # day after, before 9 AM Central
    (date(2026, 11, 20), 9, NOV19),            # day after, 9 AM: due
    (date(2026, 11, 22), 12, NOV19),           # a missed day is still sent inside the window
    (date(2026, 11, 27), 12, None),            # window over (>7 days), dropped
    (date(2026, 12, 4), 9, DEC3),
    (date(2026, 12, 18), 9, DEC17),
    (date(2026, 11, 10), 12, None),            # nothing has passed yet
])
def test_reminder_due(today, hour, want):
    assert r.reminder_due(today, hour) == want


def test_next_after():
    assert r.next_after(NOV19) == DEC3 and r.next_after(DEC3) == DEC17 and r.next_after(DEC17) is None


@pytest.mark.parametrize("value,today,clear", [
    ("November 19, 2026", date(2026, 11, 10), True),
    ("November 19, 2026", date(2026, 11, 19), True),       # on the day itself it is still the date
    ("", date(2026, 11, 10), False),
    ("TBD", date(2026, 11, 10), False),
    ("November 19", date(2026, 11, 10), False),            # no year = not clear
    ("November 19, 2026", date(2026, 11, 21), False),      # passed
    ("December 17, 2026", date(2026, 12, 18), False),      # nothing left in the list either
])
def test_missing_reason(value, today, clear):
    assert (r.missing_reason(value, today) is None) is clear


def test_reminder_text_says_what_to_do_and_never_uses_the_banned_word():
    subj, body = r.build_reminder(NOV19, DEC3, [DEC3, DEC17])
    assert "Thursday, December 3, 2026" in subj and "Eventbrite" in body and "Settings" in body and "November 19" in body
    assert "free" not in (subj + body).lower()
    subj2, body2 = r.build_reminder(DEC17, None, [])
    assert "no next Open House date" in subj2 and "Ali" in body2 and "free" not in body2.lower()


# ── the service: one send per Open House, one alert per day, nothing before 9 AM, retries, and telling Kes on failure ───────────────
def _now(y, m, d, h):                                      # Central wall time -> aware datetime
    return datetime(y, m, d, h, 0, tzinfo=ZoneInfo("America/Chicago"))


def _run(now, setting="", claimed=None, fail=None, fails=None):
    """fail: addresses whose send raises ('ALL' = every send raises). fails: failure counters, kept between runs like the database."""
    claimed = set() if claimed is None else claimed
    fails = {} if fails is None else fails
    fail = fail or set()
    sent, alerts, notices, exceptions = [], [], [], []

    def claim(session, key, n):
        if key in claimed:
            return False
        claimed.add(key)
        return True

    def blocked(addrs):
        return "ALL" in fail or any(a in fail for a in addrs)

    def smtp(settings, to, subject, body, log_label, **kw):
        if blocked(to):
            raise RuntimeError("smtp down")
        (notices if log_label == "open_house_email_failed" else sent).append((to, subject, body))

    def alert(**kw):
        if blocked(kw["to_override"]):
            raise RuntimeError("smtp down")
        alerts.append(kw)

    def bump(session, key, n):
        fails[key] = fails.get(key, 0) + 1
        return fails[key]

    settings = NS(alert_email_to="kes@colaberry.com")
    with patch.object(svc, "_claim", claim), patch.object(svc, "_release", lambda s, k: claimed.discard(k)), \
            patch.object(svc, "_bump_failures", bump), \
            patch("app.core.app_config.get_str", return_value=setting), \
            patch("app.worker.exceptions.create_exception", lambda *a, **k: exceptions.append(k)), \
            patch("app.services.alerting._smtp_send", smtp), patch("app.services.alerting._send_alert_email", alert):
        done = svc.run_open_house_reminders(MagicMock(), settings, now)
    return done, sent, alerts, claimed, notices, exceptions, fails


def test_reminder_goes_to_kes_and_jackie_once():
    done, sent, alerts, claimed, notices, exc, _ = _run(_now(2026, 11, 20, 9), setting="")
    assert done["reminder"] == "2026-11-19" and len(sent) == 1
    assert sent[0][0] == ["kes@colaberry.com", "jackie@colaberry.com"]
    assert "December 3" in sent[0][1]
    assert alerts == [] and notices == [] and exc == []     # the reminder covers the blank setting that day
    done2, sent2, *_ = _run(_now(2026, 11, 20, 9), setting="", claimed=claimed)
    assert sent2 == [] and done2["reminder"] is None       # replay / second worker: no duplicate


def test_nothing_before_nine():
    done, sent, alerts, *_ = _run(_now(2026, 11, 20, 8), setting="")
    assert sent == [] and alerts == [] and done == {"reminder": None, "missing_alert": None}


def test_alert_when_no_clear_date_starts_the_day_after_the_reminder_and_repeats_daily():
    _, sent, alerts, claimed, *_ = _run(_now(2026, 11, 21, 9), setting="")
    assert len(alerts) == 1 and "No clear Open House date" in alerts[0]["message"]
    assert alerts[0]["to_override"] == ["kes@colaberry.com", "jackie@colaberry.com"]
    _, _, again, claimed, *_ = _run(_now(2026, 11, 21, 15), setting="", claimed=claimed)
    assert again == []                                     # once a day
    _, _, next_day, *_ = _run(_now(2026, 11, 22, 9), setting="", claimed=claimed)
    assert len(next_day) == 1


def test_no_alert_when_the_date_is_clear():
    _, sent, alerts, *_ = _run(_now(2026, 11, 10, 12), setting="November 19, 2026")
    assert sent == [] and alerts == []


def test_one_bad_address_does_not_block_the_other_and_kes_is_told():
    done, sent, _, claimed, notices, exc, _ = _run(_now(2026, 11, 20, 9), fail={"jackie@colaberry.com"})
    assert done["reminder"] == "2026-11-19"
    assert [s[0] for s in sent] == [["kes@colaberry.com"]]               # Kes still got it
    assert len(exc) == 1 and "jackie@colaberry.com" in exc[0]["context"]["message"]
    assert notices and "jackie@colaberry.com" in notices[0][2]
    assert "open_house_reminder:2026-11-19" in claimed                    # no duplicate send later


def test_total_failure_retries_then_tells_kes_and_stops():
    claimed, fails = set(), {}
    for _ in range(1, svc.MAX_ATTEMPTS):
        done, _, _, claimed, notices, exc, fails = _run(_now(2026, 11, 20, 9), claimed=claimed, fails=fails, fail={"ALL"})
        assert done["reminder"] is None and exc == [] and "open_house_reminder:2026-11-19" not in claimed   # claim released, quiet retry
    done, _, _, claimed, notices, exc, fails = _run(_now(2026, 11, 20, 9), claimed=claimed, fails=fails, fail={"ALL"})
    assert len(exc) == 1 and "did not reach" in exc[0]["context"]["message"]                                  # 5th failure: dashboard record
    assert "open_house_reminder:2026-11-19" in claimed                                                         # stops looping


def test_a_retry_that_works_sends_it():
    claimed, fails = set(), {}
    _, _, _, claimed, _, _, fails = _run(_now(2026, 11, 20, 9), claimed=claimed, fails=fails, fail={"ALL"})
    done, sent, *_ = _run(_now(2026, 11, 20, 9), claimed=claimed, fails=fails)
    assert done["reminder"] == "2026-11-19" and len(sent) == 1


def test_missing_alert_failure_is_reported_too():
    claimed, fails = set(), {}
    for _ in range(svc.MAX_ATTEMPTS):
        done, _, _, claimed, _, exc, fails = _run(_now(2026, 11, 21, 9), claimed=claimed, fails=fails, fail={"ALL"})
    assert done["missing_alert"] is None and len(exc) == 2        # the late reminder and the alert both fail and are both reported


def test_recipients_are_not_duplicated():
    assert svc.recipients(NS(alert_email_to="kes@colaberry.com, Jackie@colaberry.com")) == ["kes@colaberry.com", "Jackie@colaberry.com"]
    assert svc.recipients(NS(alert_email_to=None)) == ["jackie@colaberry.com"]
