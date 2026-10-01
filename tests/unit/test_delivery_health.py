"""Delivery health (spec/35): pure rules + (opt-in, WRONG_DATE_TEST_DATABASE_URL) the GHL sync, hand-off
matching, call outcomes, replies, and the scheduled silence alert (cc Ali)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest

from app.core import channel_health as ch

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
TH = ch.Thresholds()


# ───────────────────────── pure rules ─────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("delivered", ch.DELIVERED), ("read", ch.DELIVERED), ("undelivered", ch.FAILED), ("failed", ch.FAILED),
    ("sent", ch.PENDING), ("queued", ch.PENDING), (None, ch.PENDING), ("", ch.PENDING)])
def test_sms_outcome(raw, want):
    assert ch.sms_outcome(raw) == want


@pytest.mark.parametrize("raw,want", [
    ("delivered", ch.DELIVERED), ("opened", ch.DELIVERED), ("clicked", ch.DELIVERED),
    ("bounced", ch.FAILED), ("rejected", ch.FAILED), ("sent", ch.PENDING), (None, ch.PENDING)])
def test_email_outcome(raw, want):
    assert ch.email_outcome(raw) == want


def test_extract_twilio_error_from_a_ghl_message():
    m = {"status": "undelivered", "meta": {"error": "Error 30003 - Number unreachable or out of service."}}
    assert ch.extract_error(m).startswith("30003 Number unreachable")
    assert ch.extract_error({"status": "delivered"}) is None


@pytest.mark.parametrize("reason,dur,want", [
    ("voicemail", 32, ch.DELIVERED), ("human_goodbye", 40, ch.DELIVERED), ("human_pick_up_cut_off", 33, ch.DELIVERED),
    ("agent_goodbye", 90, ch.DELIVERED), ("undefined", 14, ch.DELIVERED), ("undefined", 2, ch.FAILED),
    ("recovery_unresolved", 0, ch.FAILED), ("Failed to start outbound call", 0, ch.FAILED), (None, 0, ch.FAILED)])
def test_call_outcome(reason, dur, want):
    assert ch.call_outcome(reason, dur) == want


def _stats(**kw):
    base = dict(channel="sms", last_delivered_at=NOW - timedelta(hours=1))
    base.update(kw)
    return ch.ChannelStats(**base)


def test_the_sms_outage_pattern_is_red_even_though_attempts_look_healthy():
    # 15,000-style: lots handed over, nothing delivered, nothing failed either (workflow matched nothing)
    s = ch.evaluate(_stats(sent=40, delivered=0, failed=0, unconfirmed=40, last_delivered_at=None), NOW, TH, 100)
    assert s.level == ch.RED and any("not confirmed" in r for r in s.reasons)


def test_silence_thresholds_are_two_and_a_half_days():
    assert ch.evaluate(_stats(last_delivered_at=NOW - timedelta(hours=59)), NOW, TH, 100).level == ch.AMBER
    assert ch.evaluate(_stats(last_delivered_at=NOW - timedelta(hours=61)), NOW, TH, 100).level == ch.RED
    assert ch.evaluate(_stats(last_delivered_at=NOW - timedelta(hours=10)), NOW, TH, 100).level == ch.GREEN
    assert ch.evaluate(_stats(last_delivered_at=NOW - timedelta(hours=40)), NOW, TH, 100).level == ch.AMBER


def test_low_delivery_rate_needs_volume_and_new_tracker_is_not_judged():
    assert ch.evaluate(_stats(sent=30, delivered=10, failed=20), NOW, TH, 100).level == ch.RED       # 33%
    assert ch.evaluate(_stats(sent=30, delivered=25, failed=5), NOW, TH, 100).level == ch.AMBER      # 83%
    assert ch.evaluate(_stats(sent=5, delivered=1, failed=4), NOW, TH, 100).level == ch.GREEN        # too few to judge
    fresh = _stats(last_delivered_at=None)
    assert ch.evaluate(fresh, NOW, TH, 2).level == ch.GREEN          # empty brand-new tracker is not a dead channel
    assert ch.evaluate(_stats(last_delivered_at=None), NOW, TH, 72).level == ch.RED


def test_muted_channels_are_grey_and_never_red():
    s = ch.evaluate(_stats(last_delivered_at=NOW - timedelta(days=9), muted_reason="paused"), NOW, TH, 100)
    assert s.level == ch.GREY


def test_some_unconfirmed_is_amber_not_red_when_others_arrive():
    s = ch.evaluate(_stats(sent=40, delivered=30, failed=0, unconfirmed=10), NOW, TH, 100)
    assert s.level == ch.AMBER


# ───────────────────────── DB-backed (opt-in) ─────────────────────────

DB_URL = os.environ.get("WRONG_DATE_TEST_DATABASE_URL")
db = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")


class FakeGHL:
    def __init__(self, convs, msgs, email_status=None):
        self.convs, self.msgs, self.email_status = convs, msgs, email_status or {}
        self.calls = 0

    def search_conversations(self, **kw):
        return self.convs if kw.get("start_after_date") is None else []

    def get_conversation_messages(self, cid, limit=20):
        self.calls += 1
        return self.msgs.get(cid, [])

    def get_email_status(self, mid):
        return self.email_status.get(mid)


def _ms(dt):
    return int(dt.timestamp() * 1000)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


@pytest.fixture()
def session():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    engine = create_engine(DB_URL)
    with Session(engine) as s:
        for t in ("channel_events", "channel_sync_state", "inbound_messages", "call_events", "scheduled_jobs", "alert_events"):
            s.execute(text(f"DELETE FROM {t}"))
        s.execute(text("DELETE FROM audit_log WHERE action = 'channel_silence_check'"))
        s.execute(text("DELETE FROM app_config WHERE key LIKE 'channel_silence%' OR key LIKE 'delivery_%'"))
        s.commit()
        yield s
        s.rollback()
    engine.dispose()


@pytest.fixture()
def settings():
    return NS(ghl_conversations_api_key="x", alert_email_to="kes@example.test", alert_dedup_window_seconds=60,
              smtp_enabled=False)


def _conv(cid, contact, last):
    return {"id": cid, "contactId": contact, "lastMessageDate": _ms(last)}


def _msg(mid, typ, direction, status, at, contact="ct1", body="hi", meta=None):
    return {"id": mid, "messageType": typ, "direction": direction, "status": status, "dateAdded": _iso(at),
            "contactId": contact, "body": body, "meta": meta or {}}


def _sync(session, settings, ghl, now=NOW):
    from app.services import delivery_sync

    return delivery_sync.sync(session, settings, now, force=True, ghl=ghl)


def _count(session, sql, **p):
    from sqlalchemy import text

    return session.execute(text(sql), p).scalar()


@db
def test_sync_records_sms_email_deliveries_and_replies_once(session, settings):
    t = NOW - timedelta(minutes=30)
    ghl = FakeGHL(
        [_conv("c1", "ct1", t)],
        {"c1": [
            _msg("m1", "TYPE_SMS", "outbound", "delivered", t),
            _msg("m2", "TYPE_SMS", "outbound", "undelivered", t, meta={"error": "Error 30003 - Number unreachable"}),
            _msg("m3", "TYPE_EMAIL", "outbound", None, t, meta={"email": {"messageIds": ["e3"]}}),
            _msg("m4", "TYPE_SMS", "inbound", "delivered", t, body="STOP"),
            _msg("m5", "TYPE_CALL", "inbound", "completed", t),
            _msg("m6", "TYPE_CALL", "outbound", "completed", t),         # outbound calls come from call_events, not GHL
        ]},
        email_status={"e3": "opened"})
    r = _sync(session, settings, ghl)
    assert r["deliveries"] == 3 and r["replies"] == 2
    rows = dict(session.execute(__import__("sqlalchemy").text(
        "SELECT external_id, outcome FROM channel_events WHERE kind='delivery'")).fetchall())
    assert rows == {"m1": "delivered", "m2": "failed", "m3": "delivered"}
    assert "30003" in _count(session, "SELECT error FROM channel_events WHERE external_id='m2'")
    assert _count(session, "SELECT count(*) FROM channel_events WHERE kind='reply'") == 2
    # the reply landed in inbound_messages, so a STOP is now visible to Cora's safeguards
    assert _count(session, "SELECT body FROM inbound_messages WHERE external_id='m4'") == "STOP"
    _sync(session, settings, ghl)                                         # idempotent
    assert _count(session, "SELECT count(*) FROM channel_events") == 5
    assert _count(session, "SELECT count(*) FROM inbound_messages") == 1


@db
def test_pending_email_status_is_rechecked_until_final(session, settings):
    t = NOW - timedelta(minutes=30)
    msgs = {"c1": [_msg("m3", "TYPE_EMAIL", "outbound", None, t, meta={"email": {"messageIds": ["e3"]}})]}
    ghl = FakeGHL([_conv("c1", "ct1", t)], msgs, {"e3": "sent"})
    _sync(session, settings, ghl)
    assert _count(session, "SELECT outcome FROM channel_events WHERE external_id='m3'") == "pending"
    ghl.email_status["e3"] = "delivered"
    _sync(session, settings, ghl, NOW + timedelta(minutes=10))
    assert _count(session, "SELECT outcome FROM channel_events WHERE external_id='m3'") == "delivered"


@db
def test_sync_is_throttled_and_marks_backfill_done(session, settings):
    from app.services import delivery_sync

    ghl = FakeGHL([_conv("c1", "ct1", NOW - timedelta(hours=1))], {"c1": []})
    _sync(session, settings, ghl)
    assert delivery_sync._state(session)["backfill_done"] is True
    assert delivery_sync.sync(session, settings, NOW + timedelta(seconds=30), ghl=ghl) == {"skipped": "throttled"}
    assert delivery_sync.tracker_age_hours(session, NOW) >= 72


def _handoff(session, channel, contact, at):
    from app.services.channel_health import record_handoff

    record_handoff(session, channel, contact, "crm_job", at)
    session.commit()


def _delivery(session, channel, contact, at, outcome="delivered", x=None):
    from sqlalchemy import text

    session.execute(text("""INSERT INTO channel_events (id, kind, channel, contact_id, external_id, outcome, event_at, checked_at)
                            VALUES (:i,'delivery',:c,:ct,:x,:o,:a,:a)"""),
                    {"i": str(uuid.uuid4()), "c": channel, "ct": contact, "x": x or str(uuid.uuid4()), "o": outcome, "a": at})
    session.commit()


@db
def test_handoffs_match_deliveries_per_contact_and_unmatched_become_unconfirmed(session, settings):
    from app.services import channel_health as svc

    base = NOW - timedelta(hours=3)
    for i in range(4):
        _handoff(session, "sms", f"c{i}", base + timedelta(minutes=i))
    _delivery(session, "sms", "c0", base + timedelta(seconds=20))                  # delivered
    _delivery(session, "sms", "c1", base + timedelta(minutes=1, seconds=20), "failed")
    # c2: nothing at all (the dead-workflow signature) ; c3: nothing
    _handoff(session, "sms", "fresh", NOW - timedelta(minutes=3))                  # too new to call unconfirmed
    s = svc.channel_stats(session, settings, "sms", NOW)
    assert (s.sent, s.delivered, s.failed, s.unconfirmed) == (5, 1, 1, 2)
    assert s.last_delivered_at is not None and s.rate == 0.5


@db
def test_calls_are_judged_from_call_events_logged_after_the_call(session, settings):
    from sqlalchemy import text

    from app.services import channel_health as svc

    def launch(contact, mins_ago):
        at = NOW - timedelta(minutes=mins_ago)
        session.execute(text("""INSERT INTO scheduled_jobs (id, job_type, entity_type, entity_id, run_at, status, payload_json, version, created_at, updated_at)
                                VALUES (:i,'launch_outbound_call','lead',:c,:a,'completed',CAST(:p AS json),0,:a,:a)"""),
                        {"i": str(uuid.uuid4()), "c": contact, "a": at, "p": '{"contact_id": "%s"}' % contact})

    def log(contact, mins_ago, reason, dur):
        at = NOW - timedelta(minutes=mins_ago)
        session.execute(text("""INSERT INTO call_events (id, call_id, contact_id, direction, end_call_reason, duration_seconds, dedupe_key, created_at)
                                VALUES (:i,:i,:c,'outbound',:r,:d,:i,:a)"""),
                        {"i": str(uuid.uuid4()), "c": contact, "r": reason, "d": dur, "a": at})

    launch("a", 120); log("a", 105, "voicemail", 30)             # delivered
    launch("b", 120); log("b", 105, "recovery_unresolved", 0)    # failed
    launch("c", 120)                                             # old, no log -> unconfirmed
    launch("d", 10)                                              # log not due yet -> neither
    session.commit()
    s = svc.channel_stats(session, settings, "call", NOW)
    assert (s.sent, s.delivered, s.failed, s.unconfirmed) == (4, 1, 1, 1)
    assert svc.detail(session, "call", NOW)["items"][0]["state"] in ("failed", "not confirmed")


@db
def test_silence_check_alerts_once_copies_ali_and_resolves(session, settings):
    from app.services import channel_health as svc

    _delivery(session, "email", "x", NOW - timedelta(hours=1))
    _delivery(session, "sms", "x", NOW - timedelta(hours=70))                      # SMS silent 70 h
    from sqlalchemy import text

    session.execute(text("""INSERT INTO channel_sync_state (key, value) VALUES ('delivery_sync', CAST(:v AS jsonb))"""),
                    {"v": '{"first_sync_at": "2026-09-20T00:00:00+00:00", "backfill_done": true, "backfill_hours": 72}'})
    session.commit()
    sent = []
    with patch("app.services.alerting._smtp_send", lambda s, to, subj, body, log_label, cc_addrs=None: sent.append((to, subj, cc_addrs))):
        levels = svc.run_silence_check(session, settings, NOW, force=True)
        session.commit()
        assert levels["email"] == ch.GREEN and levels["sms"] == ch.RED
        svc.run_silence_check(session, settings, NOW + timedelta(hours=1), force=True)   # still down: no 2nd email
        session.commit()
        _delivery(session, "sms", "x", NOW + timedelta(hours=2))
        svc.run_silence_check(session, settings, NOW + timedelta(hours=2, minutes=5), force=True)
        session.commit()
    sms_mails = [m for m in sent if "SMS" in m[1]]
    assert [("CRITICAL" in m[1]) for m in sms_mails] == [True, False] and "RESOLVED" in sms_mails[1][1]
    assert all(m[2] == ["ali@colaberry.com"] and m[0] == ["kes@example.test"] for m in sms_mails)


@db
def test_silence_check_runs_at_most_hourly_and_muting_silences_a_channel(session, settings):
    from sqlalchemy import text

    from app.services import channel_health as svc

    session.execute(text("INSERT INTO channel_sync_state (key, value) VALUES ('delivery_sync', CAST(:v AS jsonb))"),
                    {"v": '{"first_sync_at": "2026-09-20T00:00:00+00:00", "backfill_done": true, "backfill_hours": 72}'})
    session.execute(text("INSERT INTO app_config (key, value, updated_by) VALUES ('channel_silence_muted','sms,email,call','t') "
                         "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"))
    session.commit()
    with patch("app.services.alerting._smtp_send") as mail:
        first = svc.run_silence_check(session, settings, NOW, force=True)
        session.commit()
        assert set(first.values()) == {ch.GREY} and not mail.called
        assert svc.run_silence_check(session, settings, NOW + timedelta(minutes=10)) == {}      # hourly throttle
    session.execute(text("DELETE FROM app_config WHERE key='channel_silence_muted'"))
    session.commit()
