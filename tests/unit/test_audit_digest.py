"""Daily digest of what the content audit cleared (Ali approved 2026-11-11). No DB, no real mail."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from app.core import audit_digest as r
from app.services import audit_digest as svc
from app.services import open_house_reminder as sender

CT = ZoneInfo("America/Chicago")
DAY = date(2026, 11, 12)


def _now(d, h):                                                  # Central wall time -> aware datetime
    return datetime(2026, 11, d, h, 0, tzinfo=CT)


def _item(i, text="We do not guarantee jobs.", at=None):
    return {"at": at or datetime(2026, 11, 12, 15, i % 60, tzinfo=timezone.utc), "channel": "email", "contact_id": f"c{i}",
            "terms": ["jobs"], "votes": ["not_offering", "not_offering"], "reason": "It only denies a result.", "text": text}


# ── pure rules ─────────────────────────────────────────────────────────────
def test_nothing_before_eight_central_then_yesterday():
    assert r.digest_day(_now(13, 7)) is None
    assert r.digest_day(_now(13, 8)) == DAY
    assert r.digest_day(datetime(2026, 11, 13, 14, 30, tzinfo=timezone.utc)) == DAY      # 8:30 Central written in UTC


def test_window_is_the_central_day():
    s, e = r.window(DAY)
    assert s.astimezone(timezone.utc) == datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)      # CST = UTC-6
    assert e.astimezone(timezone.utc) == datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)


def test_empty_digest_still_says_the_check_ran():
    subject, body = r.build_digest(DAY, [])
    assert "nothing cleared" in subject and "2026-11-12" in subject
    assert "sent every day" in body


def test_digest_shows_text_words_reason_and_votes():
    subject, body = r.build_digest(DAY, [_item(1, text="Hello  there,\nwe do not guarantee jobs.")])
    assert subject.startswith("Cora audit digest: 1 cleared")
    assert "Words that flagged it: jobs" in body and "It only denies a result." in body
    assert "Hello there, we do not guarantee jobs." in body                     # whitespace collapsed
    assert "reads: not_offering, not_offering" in body and "contact c1" in body and "Thursday, November 12, 2026" in body


def test_a_long_text_is_cut_and_a_missing_text_says_so():
    _, body = r.build_digest(DAY, [_item(1, text="x" * 2000), _item(2, text=None)])
    assert "x" * r.TEXT_CHARS in body and "x" * (r.TEXT_CHARS + 1) not in body
    assert "text not stored" in body


def test_the_list_is_capped_and_the_rest_counted():
    subject, body = r.build_digest(DAY, [_item(i) for i in range(r.MAX_LISTED + 7)])
    assert f"{r.MAX_LISTED + 7} cleared" in subject
    assert f"{r.MAX_LISTED}. " in body and f"{r.MAX_LISTED + 1}. " not in body
    assert "and 7 more not listed" in body


def test_the_digest_never_uses_the_banned_word():
    _, body = r.build_digest(DAY, [_item(1)])
    assert "free" not in body.lower().replace("full-time", "")


# ── recipients ─────────────────────────────────────────────────────────────
def test_recipients_are_kes_and_ali_without_duplicates_and_can_be_overridden():
    assert svc.recipients(NS(alert_email_to="kes@colaberry.com")) == ["kes@colaberry.com", "ali@colaberry.com"]
    assert svc.recipients(NS(alert_email_to="kes@colaberry.com, ALI@colaberry.com")) == ["kes@colaberry.com", "ALI@colaberry.com"]
    assert svc.recipients(NS(alert_email_to="kes@colaberry.com"), "x@y.com") == ["x@y.com"]
    assert svc.recipients(NS(alert_email_to=None)) == ["ali@colaberry.com"]


# ── sender: once a day, retried on failure, never silent ───────────────────
def _run(now, claimed=None, fail=False, fails=None, enabled="true", items=None):
    claimed = set() if claimed is None else claimed
    fails = {} if fails is None else fails
    sent, notices, exceptions, queries = [], [], [], []

    def claim(session, key, n):
        if key in claimed:
            return False
        claimed.add(key)
        return True

    def smtp(settings, to, subject, body, log_label, **kw):
        if fail and log_label == "audit_digest":
            raise RuntimeError("smtp down")
        (notices if log_label == "open_house_email_failed" else sent).append((to, subject, body))

    def bump(session, key, n):
        fails[key] = fails.get(key, 0) + 1
        return fails[key]

    def cleared(session, day):
        queries.append(day)
        return items if items is not None else [_item(1)]

    def get_str(key, session, settings, default=""):
        return enabled if key == svc.ENABLED_KEY else ""

    with patch.object(sender, "_claim", claim), patch.object(sender, "_release", lambda s, k: claimed.discard(k)), \
            patch.object(sender, "_bump_failures", bump), patch.object(svc, "_already_sent", lambda s, k: k in claimed), \
            patch.object(svc, "cleared_items", cleared), patch("app.core.app_config.get_str", get_str), \
            patch("app.worker.exceptions.create_exception", lambda *a, **k: exceptions.append(k)), \
            patch("app.services.alerting._smtp_send", smtp):
        done = svc.run_audit_digest(MagicMock(), NS(alert_email_to="kes@colaberry.com"), now)
    return done, sent, notices, exceptions, claimed, fails, queries


def test_digest_goes_to_kes_and_ali_once_a_day():
    done, sent, notices, exc, claimed, *_ = _run(_now(13, 8))
    assert done["sent"] == "2026-11-12" and len(sent) == 1
    assert sent[0][0] == ["kes@colaberry.com", "ali@colaberry.com"] and "1 cleared on 2026-11-12" in sent[0][1]
    assert notices == [] and exc == []
    done2, sent2, *_ = _run(_now(13, 9), claimed=claimed)                    # a later cycle or a second worker
    assert sent2 == [] and done2["sent"] is None


def test_nothing_before_eight_and_no_query_once_sent():
    done, sent, _, _, _, _, queries = _run(_now(13, 7))
    assert sent == [] and done["sent"] is None and queries == []
    _, _, _, _, claimed, _, _ = _run(_now(13, 8))
    _, _, _, _, _, _, queries2 = _run(_now(13, 9), claimed=claimed)
    assert queries2 == []                                                      # the 60-second cycle must not query all day


def test_a_quiet_day_still_sends_the_short_confirmation():
    done, sent, *_ = _run(_now(13, 8), items=[])
    assert done["sent"] == "2026-11-12" and "nothing cleared" in sent[0][1]


def test_switched_off():
    done, sent, *_ = _run(_now(13, 8), enabled="false")
    assert done["sent"] is None and sent == []


def test_failure_retries_then_tells_kes_and_stops():
    claimed, fails = set(), {}
    for _ in range(sender.MAX_ATTEMPTS - 1):
        done, sent, notices, exc, *_ = _run(_now(13, 8), claimed=claimed, fail=True, fails=fails)
        assert done["sent"] is None and notices == [] and claimed == set()       # claim released: tried again next cycle
    done, sent, notices, exc, *_ = _run(_now(13, 8), claimed=claimed, fail=True, fails=fails)
    assert done["sent"] is None and len(notices) == 1 and len(exc) == 1
    assert notices[0][1] == "Cora could not send the daily audit digest" and "daily audit digest did not go out" in notices[0][2]
    assert claimed == {"audit_digest:2026-11-12"}                                # kept: it does not loop forever


def test_a_retry_that_works_sends_it():
    claimed, fails = set(), {}
    _run(_now(13, 8), claimed=claimed, fail=True, fails=fails)
    done, sent, *_ = _run(_now(13, 8), claimed=claimed, fail=False, fails=fails)
    assert done["sent"] == "2026-11-12" and len(sent) == 1
