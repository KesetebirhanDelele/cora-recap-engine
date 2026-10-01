"""
DB-backed tests for the SMS safeguards (spec/32): DND / STOP / opt-out blocking, replied-lead
hold, campaign + TCPA sending window, pacing only between real sends, daily cap.
Opt-in: WRONG_DATE_TEST_DATABASE_URL (throwaway DB only).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text

from tests.unit.test_wrong_date_monitor import (  # noqa: F401  (fixtures)
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

from app.core.sms_eligibility import HOLD  # noqa: E402
from app.services import wrong_date_monitor as wdm  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_lead_tables(session):
    for t in ("lead_state", "inbound_messages"):
        session.execute(text(f"DELETE FROM {t}"))
    session.commit()
    yield
    for t in ("lead_state", "inbound_messages"):
        session.execute(text(f"DELETE FROM {t}"))
    session.commit()


def _lead_state(session, contact, **cols):
    cols = {"do_not_call": False, "invalid": False, **cols}
    names = ", ".join(cols)
    marks = ", ".join(f":{c}" for c in cols)
    session.execute(
        text(f"INSERT INTO lead_state (id, contact_id, version, {names}) VALUES (:id, :c, 0, {marks})"),
        {"id": str(uuid.uuid4()), "c": contact, **cols},
    )
    session.commit()


def _inbound(session, contact, body):
    session.execute(
        text("INSERT INTO inbound_messages (id, contact_id, channel, body) VALUES (:i, :c, 'sms', :b)"),
        {"i": str(uuid.uuid4()), "c": contact, "b": body},
    )
    session.commit()


def _setup(session, settings, contacts):
    for c in contacts:
        _add_msg(session, STALE_CLASS, contact=c)
    _scan(session, settings)


def _status(session, contact):
    return session.execute(text(
        "SELECT status, resolved_by FROM wrong_date_incidents WHERE contact_id=:c"), {"c": contact}).fetchone()


def _writes(ghl_cls):
    return [c.args[0] for c in ghl_cls.return_value.update_contact_fields.call_args_list]


def _run_bulk(session, settings, ghl_cls, channel="email"):
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        return wdm.send_corrections_bulk(session, settings, "kes", channel=channel)


def _sms_on(session):
    session.execute(text("""INSERT INTO app_config (key, value, updated_by) VALUES ('sms_corrections_enabled','true','t')
                            ON CONFLICT (key) DO UPDATE SET value='true'"""))
    session.commit()


# ── GHL DND ──────────────────────────────────────────────────────────────────

def test_ghl_dnd_lead_is_blocked_and_others_still_sent(session, settings):
    _setup(session, settings, ["dnd-lead", "ok-lead"])
    ghl_cls = MagicMock()

    def contact(cid):
        return _clean_contact(dnd=True) if cid == "dnd-lead" else _clean_contact()

    ghl_cls.return_value.get_contact.side_effect = contact
    r = _run_bulk(session, settings, ghl_cls)
    assert (r["sent"], r["blocked"], r["held"]) == (1, 1, 0)
    assert _writes(ghl_cls) == ["ok-lead"]                       # DND lead never written to
    status, by = _status(session, "dnd-lead")
    assert status == "dismissed" and by.startswith("auto-skip: GHL DND")


def test_email_dnd_blocks_an_email_but_sms_only_dnd_does_not(session, settings):
    _setup(session, settings, ["email-dnd", "sms-dnd"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.side_effect = lambda cid: _clean_contact(
        dndSettings={"Email": {"status": "active"}} if cid == "email-dnd" else {"SMS": {"status": "active"}}
    )
    r = _run_bulk(session, settings, ghl_cls)
    assert (r["sent"], r["blocked"]) == (1, 1)
    assert _writes(ghl_cls) == ["sms-dnd"]


def test_sms_dnd_blocks_a_text_but_email_only_dnd_does_not(session, settings):
    _sms_on(session)
    _setup(session, settings, ["sms-dnd", "email-dnd"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.side_effect = lambda cid: _clean_contact(
        dndSettings={"SMS": {"status": "active"}} if cid == "sms-dnd" else {"Email": {"status": "active"}}
    )
    r = _run_bulk(session, settings, ghl_cls, channel="sms")
    assert (r["sent"], r["blocked"]) == (1, 1)
    assert _writes(ghl_cls) == ["email-dnd"]


def test_contact_without_an_email_address_is_not_emailed(session, settings):
    _setup(session, settings, ["no-email"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.return_value = _clean_contact(email="")
    r = _run_bulk(session, settings, ghl_cls)
    assert r["blocked"] == 1 and _writes(ghl_cls) == []
    assert "no email address" in _status(session, "no-email")[1]


def test_opt_out_tag_blocks(session, settings):
    _setup(session, settings, ["tagged"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.return_value = _clean_contact(tags=["Unsubscribed"])
    r = _run_bulk(session, settings, ghl_cls)
    assert r["sent"] == 0 and r["blocked"] == 1 and _writes(ghl_cls) == []


def test_contact_that_cannot_be_read_is_not_texted(session, settings):
    _setup(session, settings, ["unreadable"])
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.side_effect = RuntimeError("GHL 500")
    r = _run_bulk(session, settings, ghl_cls)
    assert (r["sent"], r["failed"]) == (0, 1)
    assert _writes(ghl_cls) == []                                # fail closed
    assert _status(session, "unreadable")[0] == "open"           # retry later


# ── Cora-side signals (no GHL call needed) ───────────────────────────────────

@pytest.mark.parametrize("cols", [
    {"do_not_call": True}, {"invalid": True}, {"status": "closed"}, {"sales_outcome": "not_interested"},
])
def test_cora_flags_block_without_calling_ghl(session, settings, cols):
    _setup(session, settings, ["flagged"])
    _lead_state(session, "flagged", **cols)
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls)
    assert r["blocked"] == 1 and r["sent"] == 0
    ghl_cls.return_value.get_contact.assert_not_called()
    assert _status(session, "flagged")[0] == "dismissed"


def test_stop_reply_blocks_sms_but_not_email(session, settings):
    _sms_on(session)
    _setup(session, settings, ["stopper"])
    _inbound(session, "stopper", "STOP")
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls, channel="sms")
    assert r["blocked"] == 1 and _writes(ghl_cls) == []
    assert "STOP" in _status(session, "stopper")[1]


def test_sms_stop_reply_does_not_stop_an_email_correction(session, settings):
    _setup(session, settings, ["stopper2"])
    _inbound(session, "stopper2", "STOP")          # an SMS opt-out; email is a different channel
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls)
    assert r["sent"] == 1 and _writes(ghl_cls) == ["stopper2"]


def test_lead_who_replied_is_held_for_a_human_not_dismissed(session, settings):
    _setup(session, settings, ["replied"])
    _lead_state(session, "replied", last_replied_at=datetime.now(timezone.utc))
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls)
    assert (r["sent"], r["held"], r["blocked"]) == (0, 1, 0)
    assert _status(session, "replied")[0] == "open"             # stays visible for manual follow-up
    assert _writes(ghl_cls) == []


def test_blocking_closes_all_of_the_leads_incidents(session, settings):
    for ch in ("sms", "email"):
        _add_msg(session, STALE_CLASS, contact="multi", channel=ch, age_h=1 if ch == "sms" else 2)
    _scan(session, settings)
    _lead_state(session, "multi", do_not_call=True)
    _run_bulk(session, settings, MagicMock())
    assert _incident_ids(session, "open") == []
    assert len(_incident_ids(session, "dismissed")) == 2


# ── sending window ───────────────────────────────────────────────────────────

def test_window_closed_holds_lead_and_reports_next_open(session, settings):
    from app.core.sms_eligibility import Verdict

    _setup(session, settings, ["late"])
    ghl_cls = MagicMock()
    from unittest.mock import patch
    with patch.object(wdm, "_window_verdict",
                      return_value=Verdict(HOLD, "outside the allowed sending window",
                                           kind="window", next_open="2026-10-01T09:00:00-05:00")):
        r = _run_bulk(session, settings, ghl_cls)
    assert (r["sent"], r["held"], r["blocked"], r["remaining"]) == (0, 1, 0, 0)
    assert r["next_window_opens"] == "2026-10-01T09:00:00-05:00"
    assert _writes(ghl_cls) == [] and _status(session, "late")[0] == "open"
    ghl_cls.return_value.get_contact.assert_not_called()         # cheap: no GHL traffic while closed


def test_real_window_logic_campaign_hours_and_tcpa(session, settings):
    """Un-mock the window and drive it with explicit times (Chicago)."""
    from zoneinfo import ZoneInfo

    chi = ZoneInfo("America/Chicago")
    real = wdm._window_verdict.__wrapped__ if hasattr(wdm._window_verdict, "__wrapped__") else None
    assert real is None  # patched in fixture; import the original function directly
    import importlib

    orig = importlib.reload(wdm)._window_verdict            # fresh, unpatched copy
    session.execute(text("""INSERT INTO app_config (key, value, updated_by) VALUES
        ('cold_lead_active_days','0,1,2,3,4','t'),('cold_lead_active_start_hour','9','t'),
        ('cold_lead_active_end_hour','17','t') ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"""))
    session.commit()
    lead = {"contact_id": "x", "campaign_name": "Cold Lead"}

    def at(y, m, d, h):
        return datetime(y, m, d, h, 0, tzinfo=chi).astimezone(timezone.utc)

    assert orig(session, settings, "x", lead, at(2026, 10, 6, 11)) is None            # Tue 11:00 -> open
    v = orig(session, settings, "x", lead, at(2026, 10, 6, 18))                       # Tue 18:00 -> after 17:00
    assert v.action == HOLD and v.next_open
    assert orig(session, settings, "x", lead, at(2026, 10, 10, 11)).action == HOLD    # Saturday
    assert orig(session, settings, "x", lead, at(2026, 10, 6, 7)).action == HOLD      # 7am (TCPA + window)
    # unknown campaign -> must satisfy BOTH windows (strictest)
    assert orig(session, settings, "x", None, at(2026, 10, 6, 11)) is None
    assert orig(session, settings, "x", None, at(2026, 10, 10, 11)).action == HOLD


# ── pacing + daily cap ───────────────────────────────────────────────────────

def test_pacing_only_between_real_sends_not_for_skipped_leads(session, settings, monkeypatch):
    session.execute(text("UPDATE app_config SET value='5' WHERE key='correction_email_delay_seconds'"))
    session.commit()
    sleeps = []
    monkeypatch.setattr(wdm, "_sleep", sleeps.append)
    _setup(session, settings, ["a", "b", "c", "d"])
    _lead_state(session, "b", do_not_call=True)                  # skipped, must not cost a delay
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls)
    assert r["sent"] == 3 and r["blocked"] == 1
    assert sleeps == [5.0, 5.0]                                  # 3 real sends -> 2 gaps


def test_daily_cap_stops_run_and_reports_it(session, settings):
    session.execute(text("""INSERT INTO app_config (key, value, updated_by) VALUES ('correction_email_daily_cap','2','t')
                            ON CONFLICT (key) DO UPDATE SET value='2'"""))
    session.commit()
    _setup(session, settings, ["a", "b", "c", "d", "e"])
    ghl_cls = MagicMock()
    r = _run_bulk(session, settings, ghl_cls)
    assert r["sent"] == 2 and r["daily_cap_reached"] is True and r["remaining"] == 3
    again = _run_bulk(session, settings, ghl_cls)                # still inside the rolling 24h
    assert again["sent"] == 0 and again["daily_cap_reached"] is True
    assert len(_writes(ghl_cls)) == 2


# ── single send + HTTP ───────────────────────────────────────────────────────

def test_route_reports_skipped_and_held(session, settings, client):
    _setup(session, settings, ["dnd-lead", "replied-lead"])
    _lead_state(session, "replied-lead", last_replied_at=datetime.now(timezone.utc))
    ids = {r[0]: r[1] for r in session.execute(text("SELECT contact_id, id FROM wrong_date_incidents")).fetchall()}
    ghl_cls = MagicMock()
    ghl_cls.return_value.get_contact.return_value = _clean_contact(dnd=True)
    p1, p2, p3 = _live_patches(ghl_cls)
    with p1, p2, p3:
        a = client.post("/dashboard/actions/send-date-correction", json={"incident_id": ids["dnd-lead"]})
        b = client.post("/dashboard/actions/send-date-correction", json={"incident_id": ids["replied-lead"]})
    assert a.status_code == 200 and a.json()["status"] == "skipped" and "DND" in a.json()["reason"]
    assert b.json()["status"] == "held" and b.json()["held"] is True
    assert _writes(ghl_cls) == []
