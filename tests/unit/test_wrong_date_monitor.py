"""
DB-backed tests for app/services/wrong_date_monitor.py (spec/32).

These exercise the real SQL (ON CONFLICT dedupe, atomic claim) so they need a
throwaway Postgres with migrations applied. Opt-in per repo testing rules:

    WRONG_DATE_TEST_DATABASE_URL=postgresql://postgres@localhost:55432/cora_test \
        python -m pytest tests/unit/test_wrong_date_monitor.py

Never point this at production — the fixture DELETEs from the tables it uses.
GHL and SMTP are always mocked; nothing is ever really sent.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB_URL = os.environ.get("WRONG_DATE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")

from app.services import wrong_date_monitor as wdm  # noqa: E402

CLASS = "November 12, 2026"
OH = "October 29, 2026"
GOOD = "Our next class starts November 12, 2026 and our next Open House is October 29, 2026."
STALE_CLASS = "Hi Sam! Our next class starts May 30, 2026. Want a seat?"
APPT_ONLY = "Great talking to you! We'll call you on Oct 6 to finish up."


@pytest.fixture()
def session():
    engine = create_engine(DB_URL)
    with Session(engine) as s:
        for t in ("wrong_date_incidents", "outbound_messages", "alert_events"):
            s.execute(text(f"DELETE FROM {t}"))
        s.execute(text("DELETE FROM audit_log WHERE entity_type = 'wrong_date_incident'"))
        s.execute(text("DELETE FROM app_config WHERE key IN ('correction_daily_cap', "
                       "'correction_email_daily_cap', 'sms_corrections_enabled', 'correction_test_contacts')"))
        for k, v in {
            "next_class_start": CLASS,
            "next_open_house_date": OH,
            "sender_name": "Cora from Colaberry",
            "live_open_house_link": "https://example.test/rsvp",
            "unsubscribe_text": "Text STOP to stop alerts",
            "correction_send_delay_seconds": "0",
            "correction_email_delay_seconds": "0",
        }.items():
            s.execute(text("""
                INSERT INTO app_config (key, value, updated_by) VALUES (:k, :v, 'test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """), {"k": k, "v": v})
        s.commit()
        with patch.object(wdm, "_window_verdict", return_value=None):
            yield s
        s.rollback()
    engine.dispose()


@pytest.fixture()
def settings():
    return SimpleNamespace(
        smtp_enabled=False, alert_email_to="ops@example.test", frontend_url="https://dash.test",
        ghl_field_message="Message", ghl_field_support_ticket_4="Support Ticket #4",
        ghl_field_support_ticket_2="Support Issue Ticket #2",
        ghl_writes_enabled=True, default_timezone="America/Chicago",
    )


NOW = datetime.now(tz=timezone.utc)


def _add_msg(session, body, *, contact="c1", channel="sms", status="sent", age_h=1, subject=None):
    mid = str(uuid.uuid4())
    session.execute(text("""
        INSERT INTO outbound_messages (id, contact_id, channel, subject, body, status, created_at)
        VALUES (:id, :c, :ch, :sub, :b, :st, :ts)
    """), {"id": mid, "c": contact, "ch": channel, "sub": subject, "b": body, "st": status,
           "ts": NOW - timedelta(hours=age_h)})
    session.commit()
    return mid


def _incident_ids(session, status=None):
    q = "SELECT id FROM wrong_date_incidents" + (f" WHERE status = '{status}'" if status else "")
    return [r[0] for r in session.execute(text(q)).fetchall()]


def _scan(session, settings):
    with patch("app.services.alerting._send_alert_email") as email:
        n = wdm.scan_wrong_dates(session, settings, datetime.now(tz=timezone.utc))
        session.commit()
    return n, email


# ── scan ─────────────────────────────────────────────────────────────────────

def test_scan_flags_only_wrong_class_open_house_dates(session, settings):
    _add_msg(session, GOOD, contact="ok")
    stale = _add_msg(session, STALE_CLASS, contact="bad")
    _add_msg(session, APPT_ONLY, contact="appt")
    _add_msg(session, STALE_CLASS, contact="shadow", status="shadow")   # never reached the lead
    _add_msg(session, STALE_CLASS, contact="old", age_h=48)             # outside lookback

    n, email = _scan(session, settings)

    assert n == 1
    rows = session.execute(text("SELECT outbound_message_id, contact_id, status FROM wrong_date_incidents")).fetchall()
    assert rows == [(stale, "bad", "open")]
    assert email.call_count == 1
    assert email.call_args.kwargs["alert_type"] == "wrong_date_message"
    assert "May 30, 2026" in email.call_args.kwargs["message"]
    assert "November 12, 2026" in email.call_args.kwargs["message"]
    active = session.execute(text(
        "SELECT COUNT(*) FROM alert_events WHERE alert_type='wrong_date_message' AND status='active'"
    )).scalar()
    assert active == 1


def test_scan_is_idempotent_no_duplicate_incident_or_email(session, settings):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    n, email = _scan(session, settings)
    assert n == 0
    assert email.call_count == 0
    assert len(_incident_ids(session)) == 1


def test_scan_uses_current_dashboard_dates(session, settings):
    _add_msg(session, STALE_CLASS)
    session.execute(text("UPDATE app_config SET value='May 30, 2026' WHERE key='next_class_start'"))
    session.commit()
    n, _ = _scan(session, settings)
    assert n == 0  # the "stale" date is now the configured one


def test_scan_caps_new_incidents_per_cycle(session, settings):
    for i in range(wdm.MAX_NEW_INCIDENTS_PER_CYCLE + 5):
        _add_msg(session, STALE_CLASS, contact=f"c{i}")
    n, email = _scan(session, settings)
    assert n == wdm.MAX_NEW_INCIDENTS_PER_CYCLE
    assert email.call_count == wdm.MAX_NEW_INCIDENTS_PER_CYCLE


def test_email_failure_still_records_incident(session, settings):
    _add_msg(session, STALE_CLASS)
    with patch("app.services.alerting._send_alert_email", side_effect=RuntimeError("smtp down")):
        n = wdm.scan_wrong_dates(session, settings, datetime.now(tz=timezone.utc))
        session.commit()
    assert n == 1 and len(_incident_ids(session, "open")) == 1


# ── correction text ──────────────────────────────────────────────────────────

def test_correction_text_uses_current_dates_and_rsvp(session, settings):
    txt = wdm.build_correction_text(session, settings)
    assert CLASS in txt and OH in txt                         # default channel = email
    assert "https://example.test/rsvp" in txt
    assert "Text STOP" not in txt                             # GHL adds the unsubscribe footer
    sms = wdm.build_correction_text(session, settings, "sms")
    assert CLASS in sms and OH in sms
    assert "https://example.test/rsvp" not in sms             # no long third-party link in a text
    assert "www.myfreeaiclass.com" in sms and sms.endswith("Text STOP to stop alerts")


def test_correction_message_itself_is_not_flagged(session, settings):
    from app.core.wrong_date_guard import find_wrong_dates
    txt = wdm.build_correction_text(session, settings)
    assert find_wrong_dates(txt, None, expected_class_start=CLASS, expected_open_house=OH) == []


# ── send correction ──────────────────────────────────────────────────────────

def _fake_resolve(ghl, updates):
    """label -> id, like the real resolver: {'Message:' -> 'id-Message'}."""
    return {f"id-{label}": value for label, value in updates.items()}


def _clean_contact(**overrides):
    rec = {"id": "x", "dnd": False, "dndSettings": {}, "tags": [],
           "email": "lead@example.test", "phone": "+15551230000"}
    rec.update(overrides)
    return {"contact": rec}


def _live_patches(ghl_cls):
    if not isinstance(ghl_cls.return_value.get_contact.return_value, dict):
        ghl_cls.return_value.get_contact.return_value = _clean_contact()
    return (
        patch("app.adapters.ghl.GHLClient", ghl_cls),
        patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)),
        patch("app.worker.jobs.crm_jobs._resolve_to_field_ids", side_effect=_fake_resolve),
    )


def test_send_correction_writes_ghl_field_and_closes_incident(session, settings):
    _add_msg(session, STALE_CLASS, contact="ghl-123")
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")

    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        result = wdm.send_correction(session, settings, iid, "kes")
        session.commit()

    assert result["sent"] is True and result["shadow"] is False
    ghl_cls.return_value.update_contact_fields.assert_called_once()
    args = ghl_cls.return_value.update_contact_fields.call_args
    assert args.args[0] == "ghl-123"
    written = args.args[1]
    # email is the default channel: ONLY Support Issue Ticket #2 (the email workflow's body) is written
    assert list(written) == ["id-Support Issue Ticket #2"]
    assert written["id-Support Issue Ticket #2"].startswith("Quick correction")
    row = session.execute(text(
        "SELECT email_triggered_at, sms_triggered_at, correction_channel FROM wrong_date_incidents WHERE id=:i"),
        {"i": iid}).fetchone()
    assert row[0] is not None and row[1] is None and row[2] == "email"
    row = session.execute(text(
        "SELECT status, resolved_by, correction_text FROM wrong_date_incidents WHERE id=:i"), {"i": iid}).fetchone()
    assert row[0] == "corrected" and row[1] == "kes" and CLASS in row[2]
    # history row + audit row + aggregate alert resolved
    assert session.execute(text(
        "SELECT COUNT(*) FROM outbound_messages WHERE contact_id='ghl-123' AND body LIKE 'Quick correction%'")).scalar() == 1
    assert session.execute(text(
        "SELECT COUNT(*) FROM audit_log WHERE entity_id=:i AND action='send_date_correction'"), {"i": iid}).scalar() == 1
    assert session.execute(text(
        "SELECT COUNT(*) FROM alert_events WHERE alert_type='wrong_date_message' AND status='active'")).scalar() == 0
    # the correction we just logged must not itself raise a new incident
    n, _ = _scan(session, settings)
    assert n == 0


def test_second_send_for_same_incident_is_rejected_and_sends_nothing(session, settings):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        wdm.send_correction(session, settings, iid, "kes")
        session.commit()
        with pytest.raises(wdm.IncidentNotOpen):
            wdm.send_correction(session, settings, iid, "someone-else")
    assert ghl_cls.return_value.update_contact_fields.call_count == 1


def test_ghl_failure_reopens_incident(session, settings):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")
    ghl_cls = MagicMock()
    ghl_cls.return_value.update_contact_fields.side_effect = RuntimeError("GHL 500")
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        with pytest.raises(RuntimeError):
            wdm.send_correction(session, settings, iid, "kes")
        session.commit()
    assert _incident_ids(session, "open") == [iid]
    assert session.execute(text("SELECT COUNT(*) FROM outbound_messages WHERE body LIKE 'Quick correction%'")).scalar() == 0


def test_shadow_mode_sends_nothing_and_leaves_incident_open(session, settings):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")
    ghl_cls = MagicMock()
    with patch("app.adapters.ghl.GHLClient", ghl_cls), \
         patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=False)):
        result = wdm.send_correction(session, settings, iid, "kes")
    assert result["shadow"] is True and result["sent"] is False
    ghl_cls.return_value.update_contact_fields.assert_not_called()
    assert _incident_ids(session, "open") == [iid]


def test_phone_contact_id_is_resolved_before_write(session, settings):
    _add_msg(session, STALE_CLASS, contact="+15551234567")
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")
    ghl_cls = MagicMock()
    ghl_cls.return_value.search_contact_by_phone.return_value = {"id": "real-ghl-id"}
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        wdm.send_correction(session, settings, iid, "kes")
    assert ghl_cls.return_value.update_contact_fields.call_args.args[0] == "real-ghl-id"


# ── dismiss ──────────────────────────────────────────────────────────────────

def test_dismiss_closes_incident_without_sending(session, settings):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    [iid] = _incident_ids(session, "open")
    wdm.dismiss_incident(session, iid, "kes", "handled by phone")
    session.commit()
    assert _incident_ids(session, "dismissed") == [iid]
    with pytest.raises(wdm.IncidentNotOpen):
        wdm.dismiss_incident(session, iid, "kes")
    assert session.execute(text(
        "SELECT COUNT(*) FROM alert_events WHERE alert_type='wrong_date_message' AND status='active'")).scalar() == 0


# ── HTTP routes (what the dashboard tile calls) ──────────────────────────────

@pytest.fixture()
def client(session, settings):
    from fastapi.testclient import TestClient

    from app.api.dashboard_main import app
    from app.api.deps import require_dashboard_auth
    from app.db import get_db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_dashboard_auth] = lambda: {"operator_id": "kes"}
    with patch("app.config.get_settings", return_value=settings):
        yield TestClient(app)
    app.dependency_overrides.clear()


def test_route_list_send_and_dismiss(session, settings, client):
    _add_msg(session, STALE_CLASS, contact="r1")
    _add_msg(session, STALE_CLASS.replace("May 30", "Jun 2"), contact="r2")
    _scan(session, settings)

    r = client.get("/dashboard/wrong-dates")
    assert r.status_code == 200
    body = r.json()
    assert body["open_count"] == 2
    assert body["expected"] == {"class_start": CLASS, "open_house": OH}
    assert "Quick correction" in body["correction_preview"]
    first, second = body["incidents"][0]["id"], body["incidents"][1]["id"]

    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        ok = client.post("/dashboard/actions/send-date-correction", json={"incident_id": first})
        again = client.post("/dashboard/actions/send-date-correction", json={"incident_id": first})
    assert ok.status_code == 200 and ok.json()["status"] == "sent"
    assert again.status_code == 409
    assert ghl_cls.return_value.update_contact_fields.call_count == 1

    assert client.post("/dashboard/actions/dismiss-wrong-date", json={"incident_id": second}).status_code == 200
    assert client.get("/dashboard/wrong-dates").json()["open_count"] == 0
    assert len(client.get("/dashboard/wrong-dates?status=corrected").json()["incidents"]) == 1


def test_route_ghl_failure_returns_502_and_keeps_incident_open(session, settings, client):
    _add_msg(session, STALE_CLASS)
    _scan(session, settings)
    iid = client.get("/dashboard/wrong-dates").json()["incidents"][0]["id"]
    ghl_cls = MagicMock()
    ghl_cls.return_value.update_contact_fields.side_effect = RuntimeError("boom")
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = client.post("/dashboard/actions/send-date-correction", json={"incident_id": iid})
    assert r.status_code == 502
    assert client.get("/dashboard/wrong-dates").json()["open_count"] == 1


def test_route_invalid_status_filter_rejected(client):
    assert client.get("/dashboard/wrong-dates?status=bogus").status_code == 422
