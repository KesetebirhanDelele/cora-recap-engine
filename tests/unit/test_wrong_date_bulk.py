"""
DB-backed tests for the Wrong Date Monitor bulk actions (spec/32):
sibling auto-close, bulk dismiss, bulk correction send.

Opt-in like test_wrong_date_monitor.py (WRONG_DATE_TEST_DATABASE_URL, throwaway DB only).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

from tests.unit.test_wrong_date_monitor import (  # noqa: F401  (fixtures)
    DB_URL,
    STALE_CLASS,
    _add_msg,
    _clean_contact,
    _fake_resolve,
    _incident_ids,
    _live_patches,
    _scan,
    client,
    session,
    settings,
)

pytestmark = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")

from app.services import wrong_date_monitor as wdm  # noqa: E402


def _open_by_contact(session):
    return dict(session.execute(text(
        "SELECT contact_id, COUNT(*) FROM wrong_date_incidents WHERE status='open' GROUP BY 1")).fetchall())


# ── sibling auto-close ───────────────────────────────────────────────────────

def test_send_correction_closes_leads_other_open_incidents(session, settings):
    _add_msg(session, STALE_CLASS, contact="lead-a", channel="sms")
    _add_msg(session, STALE_CLASS, contact="lead-a", channel="email", age_h=2)
    _add_msg(session, STALE_CLASS, contact="lead-b")
    _scan(session, settings)
    a_ids = [r[0] for r in session.execute(text(
        "SELECT id FROM wrong_date_incidents WHERE contact_id='lead-a'")).fetchall()]
    assert len(a_ids) == 2

    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        result = wdm.send_correction(session, settings, a_ids[0], "kes")
        session.commit()

    assert result["also_closed"] == 1
    assert ghl_cls.return_value.update_contact_fields.call_count == 1   # ONE sms for lead-a
    assert _open_by_contact(session) == {"lead-b": 1}                    # lead-b untouched


# ── bulk dismiss ─────────────────────────────────────────────────────────────

def test_settings_changed_at_and_dismiss_open_before(session, settings):
    old = _add_msg(session, STALE_CLASS, contact="old", age_h=5)
    _add_msg(session, STALE_CLASS, contact="new", age_h=1)
    _scan(session, settings)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=2)

    assert wdm.count_open_before(session, cutoff) == 1
    assert wdm.dismiss_open_before(session, cutoff, "kes") == 1
    session.commit()

    assert _open_by_contact(session) == {"new": 1}
    assert session.execute(text(
        "SELECT status FROM wrong_date_incidents WHERE outbound_message_id=:m"), {"m": old}).scalar() == "dismissed"
    assert wdm.dismiss_open_before(session, cutoff, "kes") == 0         # idempotent
    assert session.execute(text(
        "SELECT COUNT(*) FROM audit_log WHERE action='bulk_dismiss_wrong_date'")).scalar() == 1

    session.execute(text("UPDATE app_config SET updated_at = now() WHERE key = 'next_class_start'"))
    session.commit()
    assert wdm.settings_changed_at(session) is not None


# ── bulk send ────────────────────────────────────────────────────────────────

def test_bulk_send_one_sms_per_lead_and_resolves_field_once(session, settings):
    for c in ("l1", "l2", "l3"):
        _add_msg(session, STALE_CLASS, contact=c)
        _add_msg(session, STALE_CLASS, contact=c, channel="email", age_h=2)   # 2 incidents per lead
    _scan(session, settings)
    assert len(_incident_ids(session, "open")) == 6

    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.return_value = _clean_contact()
    resolve = MagicMock(side_effect=_fake_resolve)
    with patch("app.adapters.ghl.GHLClient", ghl_cls), \
         patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)), \
         patch("app.worker.jobs.crm_jobs._resolve_to_field_ids", resolve):
        r = wdm.send_corrections_bulk(session, settings, "kes")

    assert (r["sent"], r["failed"], r["remaining"], r["total_leads"]) == (3, 0, 0, 3)
    assert ghl_cls.return_value.update_contact_fields.call_count == 3        # 3 SMS, not 6
    assert resolve.call_count == 2                        # subject + body field ids looked up once for the run
    assert _incident_ids(session, "open") == []
    assert len(_incident_ids(session, "corrected")) == 6
    # a second press finds nothing to do
    with patch("app.adapters.ghl.GHLClient", ghl_cls), \
         patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=True)):
        again = wdm.send_corrections_bulk(session, settings, "kes")
    assert again["sent"] == 0 and again["total_leads"] == 0
    assert ghl_cls.return_value.update_contact_fields.call_count == 3


def test_bulk_send_respects_per_call_cap_and_reports_remaining(session, settings, monkeypatch):
    monkeypatch.setattr(wdm, "BULK_SEND_MAX_LEADS", 2)
    for i in range(5):
        _add_msg(session, STALE_CLASS, contact=f"l{i}")
    _scan(session, settings)
    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        first = wdm.send_corrections_bulk(session, settings, "kes")
        second = wdm.send_corrections_bulk(session, settings, "kes")
        third = wdm.send_corrections_bulk(session, settings, "kes")
    assert [first["sent"], second["sent"], third["sent"]] == [2, 2, 1]
    assert [first["remaining"], second["remaining"], third["remaining"]] == [3, 1, 0]
    assert ghl_cls.return_value.update_contact_fields.call_count == 5


def test_bulk_send_partial_failure_keeps_sent_and_leaves_failed_open(session, settings):
    for c in ("l1", "l2", "l3"):
        _add_msg(session, STALE_CLASS, contact=c)
    _scan(session, settings)
    ghl_cls = MagicMock()

    def flaky(contact_id, *a, **k):
        if contact_id == "l2":
            raise RuntimeError("GHL 500")
        return {}

    ghl_cls.return_value.update_contact_fields.side_effect = flaky
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes")
    assert (r["sent"], r["failed"]) == (2, 1)
    assert _open_by_contact(session) == {"l2": 1}
    assert "l2" in r["errors"][0]


def test_bulk_send_stops_after_consecutive_failures(session, settings):
    for i in range(6):
        _add_msg(session, STALE_CLASS, contact=f"l{i}")
    _scan(session, settings)
    ghl_cls = MagicMock()
    ghl_cls.return_value.update_contact_fields.side_effect = RuntimeError("GHL down")
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        r = wdm.send_corrections_bulk(session, settings, "kes")
    assert r["stopped_early"] is True
    assert r["failed"] == wdm.BULK_SEND_MAX_CONSECUTIVE_FAILURES
    assert ghl_cls.return_value.update_contact_fields.call_count == wdm.BULK_SEND_MAX_CONSECUTIVE_FAILURES
    assert len(_incident_ids(session, "open")) == 6


def test_bulk_send_shadow_mode_sends_nothing(session, settings):
    _add_msg(session, STALE_CLASS, contact="l1")
    _scan(session, settings)
    ghl_cls = MagicMock()
    with patch("app.adapters.ghl.GHLClient", ghl_cls), \
         patch("app.core.mode_flags.get_mode_flags", return_value=SimpleNamespace(ghl_writes_enabled=False)):
        r = wdm.send_corrections_bulk(session, settings, "kes")
    assert r["shadow"] is True and r["sent"] == 0
    ghl_cls.return_value.update_contact_fields.assert_not_called()
    assert len(_incident_ids(session, "open")) == 1


# ── HTTP ─────────────────────────────────────────────────────────────────────

def test_routes_bulk_send_and_bulk_dismiss(session, settings, client):
    _add_msg(session, STALE_CLASS, contact="r1", age_h=10)
    _add_msg(session, STALE_CLASS, contact="r2", age_h=1)
    _scan(session, settings)

    body = client.get("/dashboard/wrong-dates").json()
    assert body["open_leads"] == 2
    assert body["bulk_send_max"] == wdm.BULK_SEND_MAX_LEADS
    assert body["settings_changed_at"] is not None

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=5)).isoformat()
    d = client.post("/dashboard/actions/dismiss-wrong-dates-bulk", json={"before": cutoff})
    assert d.status_code == 200 and d.json()["dismissed"] == 1

    ghl_cls = MagicMock()
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        s = client.post("/dashboard/actions/send-date-correction-all")
    assert s.status_code == 200
    assert s.json()["status"] == "done" and s.json()["sent"] == 1
    assert client.get("/dashboard/wrong-dates").json()["open_count"] == 0
