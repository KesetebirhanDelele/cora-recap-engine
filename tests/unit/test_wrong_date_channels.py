"""
DB-backed tests for correction channels (spec/32): email is the default, SMS is gated off,
per-channel ledgers/caps, email windows have no TCPA floor, and the allow-listed TEST send.
Opt-in: WRONG_DATE_TEST_DATABASE_URL (throwaway DB only).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.services import wrong_date_monitor as wdm

_ORIG_WINDOW = wdm._window_verdict      # captured before the session fixture patches it

from tests.unit.test_wrong_date_monitor import (  # noqa: E402,F401  (fixtures)
    DB_URL,
    STALE_CLASS,
    _add_msg,
    _clean_contact,
    _incident_ids,
    _live_patches,
    _scan,
    client,
    session,
    settings,
)

pytestmark = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")


def _cfg(session, key, value):
    session.execute(text("""INSERT INTO app_config (key, value, updated_by) VALUES (:k, :v, 't')
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"""), {"k": key, "v": value})
    session.commit()


def _setup(session, settings, contacts):
    for c in contacts:
        _add_msg(session, STALE_CLASS, contact=c)
    _scan(session, settings)


def _writes(ghl_cls):
    return [(c.args[0], c.args[1]) for c in ghl_cls.return_value.update_contact_fields.call_args_list]


def _ledger(session, contact):
    return session.execute(text(
        "SELECT status, correction_channel, email_triggered_at IS NOT NULL, sms_triggered_at IS NOT NULL "
        "FROM wrong_date_incidents WHERE contact_id=:c"), {"c": contact}).fetchone()


# ── SMS is gated off ─────────────────────────────────────────────────────────

def test_sms_is_disabled_by_default(session, settings):
    assert wdm.sms_enabled(session, settings) is False
    _setup(session, settings, ["a"])
    [iid] = _incident_ids(session, "open")
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        with pytest.raises(wdm.ChannelDisabled):
            wdm.send_correction(session, settings, iid, "kes", channel="sms")
        with pytest.raises(wdm.ChannelDisabled):
            wdm.send_corrections_bulk(session, settings, "kes", channel="sms")
    assert _writes(ghl_cls) == [] and _incident_ids(session, "open") == [iid]


def test_sms_writes_only_ticket4_when_enabled(session, settings):
    _cfg(session, "sms_corrections_enabled", "true")
    _setup(session, settings, ["a"])
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes", channel="sms")
    assert r["sent"] == 1 and r["channel"] == "sms"
    [(contact, fields)] = _writes(ghl_cls)
    assert list(fields) == ["id-Support Ticket #4"] and fields["id-Support Ticket #4"].endswith("Text STOP to stop alerts")
    assert _ledger(session, "a") == ("corrected", "sms", False, True)


def test_email_writes_subject_to_ticket2_and_html_body_to_message(session, settings):
    _setup(session, settings, ["a"])
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes")
    assert r["sent"] == 1 and r["channel"] == "email"
    [(contact, fields)] = _writes(ghl_cls)
    assert set(fields) == {"id-Support Issue Ticket #2", "id-Message"}        # one update, two fields
    subject, body = fields["id-Support Issue Ticket #2"], fields["id-Message"]
    assert subject == "Correction: our class and Open House dates"            # originates from the engine
    assert len(subject) < 80 and "Quick correction" not in subject
    assert body.startswith("<p>Hi there,</p>") and "November 12, 2026" in body and "October 29, 2026" in body
    assert 'href="https://example.test/rsvp"' in body and "www.myfreeaiclass.com" in body
    assert "Text STOP" not in body and subject not in body.replace("Correction:", "")
    assert _ledger(session, "a") == ("corrected", "email", True, False)
    rec = session.execute(text(
        "SELECT subject, left(body, 16) FROM outbound_messages WHERE contact_id='a' AND body LIKE '<p>Hi there,%'")).fetchone()
    assert rec == (subject, "<p>Hi there,</p>")                                # history keeps subject + html


def test_email_subject_is_configurable_and_normalised(session, settings):
    _cfg(session, "correction_email_subject", "  Updated   dates \n for you ")
    assert wdm.correction_email_subject(session, settings) == "Updated dates for you"
    _cfg(session, "correction_email_subject", "x" * 500)
    assert len(wdm.correction_email_subject(session, settings)) == 120
    _cfg(session, "correction_email_subject", "   ")
    assert wdm.correction_email_subject(session, settings) == wdm.DEFAULT_EMAIL_SUBJECT


def test_correction_email_variants_and_escaping(session, settings):
    _cfg(session, "sender_name", "Cora & Co <team>")
    _, html = wdm.build_correction_email(session, settings)
    assert "Cora &amp; Co &lt;team&gt;" in html and "<team>" not in html           # escaped
    _cfg(session, "next_open_house_date", "")
    _, html = wdm.build_correction_email(session, settings)
    assert "November 12, 2026" in html and "no Open House scheduled" in html and "RSVP here" not in html
    _cfg(session, "next_class_start", "")
    subject, html = wdm.build_correction_email(session, settings)
    assert "don't have a class or Open House scheduled" in html and "<li>" not in html
    assert "myfreeaiclass.com" in html and subject == wdm.DEFAULT_EMAIL_SUBJECT


def test_failed_write_clears_the_email_ledger(session, settings):
    _setup(session, settings, ["a"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.update_contact_fields.side_effect = RuntimeError("GHL 500")
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        wdm.send_corrections_bulk(session, settings, "kes")
    assert _ledger(session, "a") == ("open", None, False, False)


def test_missing_field_setting_fails_loudly(session, settings):
    _setup(session, settings, ["a"])
    settings.ghl_field_support_ticket_2 = None
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes")
    assert r["failed"] == 1 and "GHL_FIELD_SUPPORT_TICKET_2" in r["errors"][0]
    assert _writes(ghl_cls) == [] and _ledger(session, "a")[0] == "open"


# ── per-channel counters ─────────────────────────────────────────────────────

def test_email_and_sms_daily_counters_are_independent(session, settings):
    _cfg(session, "sms_corrections_enabled", "true")
    _cfg(session, "correction_email_daily_cap", "1")
    _setup(session, settings, ["a", "b", "c"])
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        e = wdm.send_corrections_bulk(session, settings, "kes", channel="email")
        s = wdm.send_corrections_bulk(session, settings, "kes", channel="sms")
    assert e["sent"] == 1 and e["daily_cap_reached"] is True and e["remaining"] == 2
    assert s["sent"] == 2                          # the email cap does not limit texts
    assert wdm.sent_last_24h(session, "email") == 1 and wdm.sent_last_24h(session, "sms") == 2


def test_email_has_its_own_default_cap_and_pacing(session, settings):
    assert wdm._daily_cap(session, settings, "email") == 100
    assert wdm._daily_cap(session, settings, "sms") == 300
    session.execute(text("DELETE FROM app_config WHERE key IN ('correction_email_delay_seconds', 'correction_send_delay_seconds')"))
    session.commit()
    assert wdm._send_delay(session, settings, "email") == 3.0
    assert wdm._send_delay(session, settings, "sms") == 5.0


# ── windows: TCPA hours apply to texts, not emails ───────────────────────────

def test_email_window_has_no_tcpa_floor_but_sms_does(session, settings):
    chi = ZoneInfo("America/Chicago")
    for k, v in {"new_lead_active_days": "0,1,2,3,4,5,6", "new_lead_active_start_hour": "6",
                 "new_lead_active_end_hour": "23"}.items():
        _cfg(session, k, v)
    lead = {"contact_id": "x", "campaign_name": "New Lead"}
    at_0645 = datetime(2026, 10, 7, 6, 45, tzinfo=chi).astimezone(timezone.utc)      # inside campaign hours, before 08:00
    assert _ORIG_WINDOW(session, settings, "x", lead, at_0645, tcpa=False) is None    # email: allowed
    assert _ORIG_WINDOW(session, settings, "x", lead, at_0645, tcpa=True).action == "hold"   # sms: TCPA floor


# ── per-channel contact checks ───────────────────────────────────────────────

def test_sms_needs_a_phone_and_email_needs_an_address(session, settings):
    _cfg(session, "sms_corrections_enabled", "true")
    _setup(session, settings, ["a"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.return_value = _clean_contact(phone="")
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes", channel="sms")
    assert r["blocked"] == 1 and _writes(ghl_cls) == []


# ── TEST send to the operator's own contact ──────────────────────────────────

def _test_ghl(contacts, record=None):
    ghl_cls = MagicMock()
    ghl_cls.return_value.search_contacts_by_query.return_value = contacts
    ghl_cls.return_value.get_contact.return_value = record or _clean_contact(email="me@example.test")
    return ghl_cls


def test_test_send_requires_an_allow_list(session, settings):
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}])
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        with pytest.raises(ValueError, match="No test contacts"):
            wdm.send_test_correction(session, settings, "email", "kes")
    assert _writes(ghl_cls) == []


def test_test_send_only_to_allow_listed_address(session, settings):
    _cfg(session, "correction_test_contacts", "me@example.test")
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}])
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        with pytest.raises(ValueError, match="allow-list"):
            wdm.send_test_correction(session, settings, "email", "kes", email="someone.else@example.test")
    assert _writes(ghl_cls) == []


def test_test_email_has_a_labelled_subject_and_html_body_even_with_sms_disabled(session, settings):
    _cfg(session, "correction_test_contacts", "me@example.test")
    _setup(session, settings, ["real-lead"])
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}])
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_test_correction(session, settings, "email", "kes")
        session.commit()
    assert r["sent"] is True
    [(contact, fields)] = _writes(ghl_cls)
    assert contact == "c1" and set(fields) == {"id-Support Issue Ticket #2", "id-Message"}
    subject, body = fields["id-Support Issue Ticket #2"], fields["id-Message"]
    assert subject.startswith("TEST [") and subject.endswith("Correction: our class and Open House dates")
    assert "please ignore" in body and "November 12, 2026" in body
    assert _ledger(session, "real-lead")[0] == "open"           # no incident touched
    assert wdm.sent_last_24h(session, "email") == 0
    assert session.execute(text("SELECT COUNT(*) FROM audit_log WHERE action='send_test_correction'")).scalar() >= 1


def test_test_sms_writes_only_ticket4_even_with_sms_disabled(session, settings):
    _cfg(session, "correction_test_contacts", "me@example.test")
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}])
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_test_correction(session, settings, "sms", "kes")
    assert r["sent"] is True
    [(contact, fields)] = _writes(ghl_cls)
    assert list(fields) == ["id-Support Ticket #4"] and fields["id-Support Ticket #4"].startswith("TEST [")
    assert "please ignore" in fields["id-Support Ticket #4"]


def test_test_send_needs_exactly_one_exact_match(session, settings):
    _cfg(session, "correction_test_contacts", "me@example.test")
    for contacts in ([], [{"id": "1", "email": "me@example.test"}, {"id": "2", "email": "me@example.test"}],
                     [{"id": "3", "email": "me.too@example.test"}]):
        ghl_cls = _test_ghl(contacts)
        p1, p2, p3 = _live_patches(ghl_cls)
        with p1, p2, p3:
            with pytest.raises(ValueError, match="exactly one"):
                wdm.send_test_correction(session, settings, "email", "kes")
        assert _writes(ghl_cls) == []


def test_test_send_respects_dnd(session, settings):
    _cfg(session, "correction_test_contacts", "me@example.test")
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}],
                        _clean_contact(email="me@example.test", dndSettings={"SMS": {"status": "active"}}))
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        sms = wdm.send_test_correction(session, settings, "sms", "kes")
        email = wdm.send_test_correction(session, settings, "email", "kes")
    assert sms["skipped"] is True and "SMS" in sms["reason"]
    assert email["sent"] is True                                 # SMS-only DND doesn't block the email test


# ── HTTP ─────────────────────────────────────────────────────────────────────

def test_routes_channel_gating_and_test_send(session, settings, client):
    _setup(session, settings, ["r1"])
    body = client.get("/dashboard/wrong-dates").json()
    assert body["sms_enabled"] is False and body["test_available"] is False
    assert "Text STOP" in body["correction_previews"]["sms"] and "Text STOP" not in body["correction_previews"]["email"]
    iid = body["incidents"][0]["id"]

    assert client.post("/dashboard/actions/send-date-correction",
                       json={"incident_id": iid, "channel": "sms"}).status_code == 403
    assert client.post("/dashboard/actions/send-date-correction-all", json={"channel": "sms"}).status_code == 403
    assert client.post("/dashboard/actions/send-date-correction-all", json={"channel": "fax"}).status_code == 422

    _cfg(session, "correction_test_contacts", "me@example.test")
    ghl_cls = _test_ghl([{"id": "c1", "email": "me@example.test"}])
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        t = client.post("/dashboard/actions/send-test-correction", json={"channel": "sms"})
        e = client.post("/dashboard/actions/send-test-correction", json={"channel": "email"})
        sent = client.post("/dashboard/actions/send-date-correction-all", json={})
    assert t.status_code == 200 and t.json()["status"] == "sent"
    assert e.json()["status"] == "sent"
    assert sent.json()["sent"] == 1 and sent.json()["channel"] == "email"      # empty body defaults to email


# ── request time budget (Next.js proxy drops requests open > ~30 s) ──────────

def test_bulk_request_stops_at_time_budget_and_the_tile_can_finish_in_more_calls(session, settings, monkeypatch):
    _setup(session, settings, ["a", "b", "c", "d", "e"])
    ticks = iter(range(0, 1000, 10))                  # every clock read advances 10 s
    monkeypatch.setattr(wdm, "_monotonic", lambda: next(ticks))
    monkeypatch.setattr(wdm, "BULK_TIME_BUDGET_SECONDS", 25.0)
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    totals, calls, last = 0, 0, None
    with p1, p2, p3:
        while calls < 20:
            last = wdm.send_corrections_bulk(session, settings, "kes")
            calls += 1
            totals += last["sent"]
            if last["remaining"] == 0:
                break
    assert totals == 5 and calls > 1                  # needed several short requests
    assert last["remaining"] == 0
    assert len(_writes(ghl_cls)) == 5                 # each lead emailed exactly once
    assert _incident_ids(session, "open") == []


def test_budget_never_blocks_progress(session, settings, monkeypatch):
    _setup(session, settings, ["a", "b", "c"])
    monkeypatch.setattr(wdm, "BULK_TIME_BUDGET_SECONDS", 0.0)       # always "out of time"
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        first = wdm.send_corrections_bulk(session, settings, "kes")
    assert first["sent"] == 1 and first["remaining"] == 2           # at least one lead per request
