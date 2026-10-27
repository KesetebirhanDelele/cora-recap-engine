"""spec/45: GHL writers use the live flags, the call-date parser reads epoch strings, and the repair / backfill scripts.

Found 2026-10-26: four writers (end-of-campaign finalization, enrolled leads, dashboard force-finalize, student summary) never passed the live-mode flags so every
write ran in shadow mode (38 of 40 recent finalized leads still said AI Campaign = Yes; 301 student summaries never reached GHL, 25 of 25 sampled empty), and a
13-digit epoch-ms STRING was read as the year 1793 (238 calls missing from the reports)."""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import ghl_write
from app.models import Base
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.worker.jobs.call_processing import _parse_datetime

UTC = timezone.utc
NOW = datetime.now(tz=UTC)
FIELDS = {"AI Campaign": "F-AI", "Student Summary": "F-SUM"}
LIVE, SHADOW = NS(ghl_writes_enabled=True), NS(ghl_writes_enabled=False)


# -- the call-date parser ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["1793061552134", 1793061552134, "1793061552", 1793061552, "1793061552.134"])
def test_epoch_values_in_any_shape_are_read_as_epoch(value):
    assert _parse_datetime(value) == datetime(2026, 10, 27, 0, 39, 12, tzinfo=UTC) or abs((_parse_datetime(value) - datetime(2026, 10, 27, 0, 39, 12, tzinfo=UTC)).total_seconds()) < 1


@pytest.mark.parametrize("value,want", [("2026-10-26T20:05:00Z", datetime(2026, 10, 26, 20, 5, tzinfo=UTC)),
                                       ("2026-10-26 20:05:00+00:00", datetime(2026, 10, 26, 20, 5, tzinfo=UTC)),
                                       ("2026-10-26T15:05:00-05:00", datetime(2026, 10, 26, 20, 5, tzinfo=UTC)),
                                       ("20261026", datetime(2026, 10, 26, tzinfo=UTC))])
def test_iso_strings_still_parse_as_before(value, want):
    assert _parse_datetime(value).astimezone(UTC) == want


@pytest.mark.parametrize("value", [None, "", "garbage", "0000-00-00"])
def test_unparseable_values_are_none(value):
    assert _parse_datetime(value) is None


def test_an_implausible_year_is_none_not_the_18th_century():
    assert _parse_datetime("1500-01-01T00:00:00Z") is None and _parse_datetime(-5_000_000_000) is None


# -- the shared live-write helper --------------------------------------------------------------------------------------------------
class FakeGHL:
    def __init__(self, contacts, shadow_unless_flags=True):
        self.contacts = {c["id"]: c for c in contacts}
        self.updates = []

    def get_location_fields(self):
        return [{"id": fid, "name": name} for name, fid in FIELDS.items()]

    def search_contact_by_phone(self, phone):
        d = "".join(ch for ch in phone if ch.isdigit())
        return next((c for c in self.contacts.values() if "".join(ch for ch in (c.get("phone") or "") if ch.isdigit()).endswith(d[-10:])), None)

    def update_contact_fields(self, contact_id, field_updates, *, mode_flags=None):
        if mode_flags is None or not mode_flags.ghl_writes_enabled:
            return {"shadow": True}
        self.updates.append((contact_id, dict(field_updates)))
        return {"contact": {"id": contact_id}}


def test_write_label_fields_writes_the_field_id_for_the_matched_contact_with_live_flags():
    ghl = FakeGHL([{"id": "G1", "phone": "+14693249654"}])
    assert ghl_write.write_label_fields(ghl, "+14693249654", {"Student Summary": "Hello"}, NS(), LIVE, "t") is True
    assert ghl.updates == [("G1", {"F-SUM": "Hello"})]                             # the FIELD ID, not the label


@pytest.mark.parametrize("flags,contacts,labels", [
    (SHADOW, [{"id": "G1", "phone": "+14693249654"}], {"Student Summary": "x"}),                # dashboard in shadow
    (None, [{"id": "G1", "phone": "+14693249654"}], {"Student Summary": "x"}),                  # the old call shape: no flags
    (LIVE, [{"id": "OTHER", "phone": "+19999999999"}], {"Student Summary": "x"}),               # a near-match must not receive the write
    (LIVE, [{"id": "G1", "phone": "+14693249654"}], {"Missing Field": "x"}),                    # the field does not exist in GHL
])
def test_write_label_fields_is_false_and_writes_nothing_when_it_cannot_really_write(flags, contacts, labels):
    ghl = FakeGHL(contacts)
    assert ghl_write.write_label_fields(ghl, "+14693249654", labels, NS(), flags, "t") is False
    assert ghl.updates == []


# -- the four writers -----------------------------------------------------------------------------------------------------------------
@pytest.fixture()
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def add_lead(session, contact_id="+15550001", **kw):
    lead = LeadState(id=str(uuid.uuid4()), contact_id=contact_id, normalized_phone=contact_id, status="active", version=0, created_at=NOW, updated_at=NOW, **kw)
    session.add(lead)
    session.flush()
    return lead


def test_finalization_writes_only_ai_campaign_no_with_the_live_flags(session):
    from app.worker.jobs.voicemail_jobs import _finalize_campaign
    lead = add_lead(session, ai_campaign_value="3")
    settings = NS(ghl_field_ai_campaign="AI Campaign", ghl_field_mark_as_lead="Mark as Lead", ghl_writes_enabled=False)
    with patch("app.core.mode_flags.get_mode_flags", return_value=LIVE), patch("app.adapters.ghl.GHLClient", return_value="client"), \
            patch("app.core.ghl_write.write_label_fields", return_value=True) as w:
        _finalize_campaign(session, lead, settings)
    client, cid, labels, _settings, flags, reason = w.call_args.args
    assert (client, cid, labels, flags, reason) == ("client", "+15550001", {"AI Campaign": "No"}, LIVE, "finalize")      # no "Mark as Lead"


def test_finalization_never_raises_when_the_write_does_not_happen(session):
    from app.worker.jobs.voicemail_jobs import _finalize_campaign
    lead = add_lead(session, ai_campaign_value="3")
    with patch("app.core.mode_flags.get_mode_flags", return_value=SHADOW), patch("app.adapters.ghl.GHLClient", return_value="client"), \
            patch("app.core.ghl_write.write_label_fields", return_value=False):
        _finalize_campaign(session, lead, NS(ghl_field_ai_campaign=None, ghl_field_mark_as_lead=None, ghl_writes_enabled=False))


def test_force_finalize_uses_the_live_flag_helper(session):
    from app.services.dashboard import _finalize_lead
    add_lead(session, "+15550002", ai_campaign_value="1")
    with patch("app.core.intent_actions._write_ghl_campaign_off", return_value=True) as w:
        assert _finalize_lead(session, "+15550002", NS(ghl_field_ai_campaign="AI Campaign")) is True
    assert w.call_args.args[0] == "+15550002" and w.call_args.args[2] == "force_finalize" and w.call_args.args[3] is session


# -- repair of impossible call dates ---------------------------------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location("repair_dates", Path(__file__).resolve().parents[2] / "execution" / "repair_call_dates.py")
REPAIR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REPAIR)


def add_call(session, started, payload, call_started=None, created=None):
    session.add(CallEvent(id=str(uuid.uuid4()), call_id=str(uuid.uuid4()), contact_id="c", direction="outbound", status="completed", dedupe_key=uuid.uuid4().hex,
                          raw_payload_json=payload, start_time_utc=started, call_started_at=call_started, created_at=created or NOW))
    session.flush()


def test_repair_recovers_the_real_time_from_the_payload_then_call_started_at_then_created_at(session):
    bad = datetime(1793, 6, 15, 21, 34, tzinfo=UTC)
    good = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    add_call(session, bad, {"start_time": "1793061552134"}, call_started=bad)                         # exact, from the payload
    add_call(session, bad, {}, call_started=good)                                                     # exact, from its own call_started_at
    add_call(session, bad, {}, call_started=bad, created=good)                                       # approximate, from created_at
    add_call(session, good, {"start_time": "1793061552134"}, call_started=good)                       # already fine: untouched
    lines = []
    assert REPAIR.run(session, False, lines.append)["repaired"] == 0 and "DRY RUN" in lines[-1]
    assert "payload" in lines[1] and "call_started_at" in lines[1] and "created_at" in lines[1]
    res = REPAIR.run(session, True, lambda *_: None)
    assert res["bad"] == 3 and res["repaired"] == 3
    times = sorted(c.start_time_utc.replace(tzinfo=UTC) for c in session.scalars(select(CallEvent)).all())
    assert all(t.year == 2026 for t in times)
    assert REPAIR.run(session, True, lambda *_: None)["bad"] == 0                                    # idempotent


# -- backfill of finalized leads ---------------------------------------------------------------------------------------------------------
_spec2 = importlib.util.spec_from_file_location("bf", Path(__file__).resolve().parents[2] / "execution" / "backfill_ai_campaign_off.py")
BF = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(BF)


def test_backfill_finalized_selects_only_leads_whose_campaign_ended(session):
    add_lead(session, "+15550011", ai_campaign_value="3")
    add_lead(session, "+15550012", ai_campaign_value="1")
    add_lead(session, "+15550013", ai_campaign_value="3", do_not_call=True)
    assert sorted(c for c, _ in BF.candidates(session, None, finalized=True)) == ["+15550011", "+15550013"]
    assert all(r == "finalized" for _c, r in BF.candidates(session, None, finalized=True))
    assert [c for c, _ in BF.candidates(session, None)] == ["+15550013"]                              # the default is still the opt-out list


# -- the prompts ---------------------------------------------------------------------------------------------------------------------------
def test_every_prompt_says_never_to_transfer_or_book_on_a_recording():
    from app.adapters.synthflow import _load_campaign_prompt
    from tests.unit import (
        test_update_inbound_prompt_script as t,  # noqa: F401  (the inbound builder is exercised there)
    )
    for c in ("Cold Lead", "New Lead"):
        p = _load_campaign_prompt(c)
        assert "recording, an automated menu or an answering machine" in p and "do NOT transfer, do NOT book anything" in p
    inbound = (Path(__file__).resolve().parents[2] / "docs" / "synthflow-inbound-prompt.md").read_text(encoding="utf-8")
    assert "recording, an automated menu or an answering machine" in inbound


# -- do-not-disturb reconcile drain -------------------------------------------------------------------------------------------------------
_spec3 = importlib.util.spec_from_file_location("drain", Path(__file__).resolve().parents[2] / "execution" / "drain_dnd_reconcile.py")
DRAIN = importlib.util.module_from_spec(_spec3)
_spec3.loader.exec_module(DRAIN)


def test_drain_calls_the_reconcile_step_until_nothing_is_left_and_adds_up_the_results():
    results = iter([{"checked": 20, "ok": 15, "review": 5, "failed": 0}, {"checked": 8, "ok": 8, "review": 0, "failed": 0}, {"checked": 0, "ok": 0, "review": 0, "failed": 0}])
    calls = []

    def step(session, settings, ghl, limit, budget_seconds):
        calls.append((limit, budget_seconds))
        return next(results)
    t = DRAIN.drain(None, None, step=step, out=lambda *_: None)
    assert t["checked"] == 28 and t["ok"] == 23 and t["review"] == 5 and t["steps"] == 3 and len(calls) == 3


def test_drain_stops_at_the_time_limit_even_if_work_remains():
    ticks = iter(range(0, 10_000, 600))                                 # each clock() call is 10 minutes later

    def step(*a, **k):
        return {"checked": 20, "ok": 20, "review": 0, "failed": 0}
    t = DRAIN.drain(None, None, step=step, max_minutes=25, clock=lambda: next(ticks), out=lambda *_: None)
    assert 1 <= t["steps"] <= 3
