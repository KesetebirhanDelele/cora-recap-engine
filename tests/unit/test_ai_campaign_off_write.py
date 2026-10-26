"""spec/43: the 'AI Campaign = No' write for opted-out / invalid leads must really reach GHL, only for the right contact, and say so honestly.

Found 2026-10-26: _write_ghl_campaign_off called the GHL writer without the live-mode flags (so it silently ran in shadow mode) and a
phone-number contact id cannot be updated by id; 48 of the 54 most recent do-not-call leads still said AI Campaign = Yes in GHL. The old
tests replaced the write with a stand-in, which is why nothing caught it - these use a fake GHL that behaves like the real one
(shadow unless the live flags are passed, a phone search that can return a near-match, a read-back)."""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core import intent_actions as ia
from app.models import Base
from app.models.lead_state import LeadState

NOW = datetime.now(tz=timezone.utc)
FIELD = "F-AI-CAMPAIGN"
LIVE = NS(ghl_writes_enabled=True)
SHADOW = NS(ghl_writes_enabled=False)


class FakeGHL:
    """Behaves like GHLClient where it matters: update_contact_fields is a shadow no-op unless live flags are passed; the phone search is a
    text query that can return a DIFFERENT contact; contacts can be read back."""

    def __init__(self, contacts, env_writes_enabled=False, missing_field=False):
        self.contacts = {c["id"]: c for c in contacts}
        self.env_writes_enabled, self.missing_field = env_writes_enabled, missing_field
        self.updates = []
        self.settings = NS(ghl_writes_enabled=env_writes_enabled)

    def get_location_fields(self):
        return [] if self.missing_field else [{"id": FIELD, "name": "AI Campaign"}]

    def search_contact_by_phone(self, phone):
        digits = "".join(ch for ch in phone if ch.isdigit())
        for c in self.contacts.values():
            if c.get("phone") and "".join(ch for ch in c["phone"] if ch.isdigit()).endswith(digits[-10:]):
                return c
        return next(iter(self.contacts.values()), None) if self.contacts else None          # GHL text search: a fuzzy near-match

    def get_contact(self, cid):
        return {"contact": self.contacts[cid]}

    def update_contact_fields(self, contact_id, field_updates, *, mode_flags=None):
        enabled = mode_flags.ghl_writes_enabled if mode_flags is not None else self.env_writes_enabled
        if not enabled:
            return {"shadow": True, "operation": "update_contact_fields", "contact_id": contact_id}
        self.updates.append((contact_id, dict(field_updates)))
        for fid, val in field_updates.items():
            fields = [f for f in self.contacts[contact_id].setdefault("customFields", []) if f["id"] != fid]
            self.contacts[contact_id]["customFields"] = fields + [{"id": fid, "value": [val]}]
        return {"contact": self.contacts[contact_id]}


def contact(cid, phone, value="Yes"):
    return {"id": cid, "phone": phone, "customFields": [{"id": FIELD, "value": [value]}] if value else []}


SETTINGS = NS(ghl_field_ai_campaign="AI Campaign")


# -- resolving the GHL contact -----------------------------------------------------------------------------------------------
def test_a_real_ghl_id_is_used_as_is():
    assert ia.resolve_ghl_contact_id(FakeGHL([]), "Q4qC3vxTymdVAQfwnTp5") == "Q4qC3vxTymdVAQfwnTp5"


@pytest.mark.parametrize("phone", ["+14693249654", "14693249654", "+1 469-324-9654"])
def test_a_phone_keyed_id_resolves_to_the_contact_with_that_number(phone):
    assert ia.resolve_ghl_contact_id(FakeGHL([contact("G1", "+14693249654")]), phone) == "G1"


def test_a_near_match_from_ghls_text_search_is_never_accepted():
    ghl = FakeGHL([contact("OTHER", "+19999999999")])                    # the search returns somebody else
    assert ia.resolve_ghl_contact_id(ghl, "+14693249654") is None


def test_a_phone_with_no_contact_in_ghl_resolves_to_nothing():
    assert ia.resolve_ghl_contact_id(FakeGHL([]), "+14693249654") is None
    assert ia.resolve_ghl_contact_id(FakeGHL([]), "") is None


# -- the write itself ----------------------------------------------------------------------------------------------------------
def test_live_flags_really_write_no_and_it_reads_back():
    ghl = FakeGHL([contact("G1", "+14693249654", "Yes")])
    assert ia.set_ai_campaign_no(ghl, "+14693249654", SETTINGS, LIVE, "do_not_call") is True
    assert ghl.updates == [("G1", {FIELD: "No"})]
    assert ghl.get_contact("G1")["contact"]["customFields"] == [{"id": FIELD, "value": ["No"]}]


def test_the_bug_without_live_flags_the_write_is_silently_shadowed_and_now_reported_as_false():
    ghl = FakeGHL([contact("G1", "+14693249654", "Yes")], env_writes_enabled=False)
    assert ia.set_ai_campaign_no(ghl, "+14693249654", SETTINGS, None, "do_not_call") is False      # old call shape: no flags
    assert ia.set_ai_campaign_no(ghl, "+14693249654", SETTINGS, SHADOW, "do_not_call") is False    # dashboard says shadow
    assert ghl.updates == [] and ghl.get_contact("G1")["contact"]["customFields"][0]["value"] == ["Yes"]


def test_nothing_is_written_when_the_phone_does_not_match_a_contact():
    ghl = FakeGHL([contact("OTHER", "+19999999999")])
    assert ia.set_ai_campaign_no(ghl, "+14693249654", SETTINGS, LIVE, "wrong_number") is False
    assert ghl.updates == []


def test_nothing_is_written_when_the_field_does_not_exist():
    ghl = FakeGHL([contact("G1", "+14693249654")], missing_field=True)
    assert ia.set_ai_campaign_no(ghl, "+14693249654", SETTINGS, LIVE, "wrong_number") is False
    assert ghl.updates == []


# -- the handler path ---------------------------------------------------------------------------------------------------------
@pytest.fixture()
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(LeadState(id=str(uuid.uuid4()), contact_id="+14693249654", normalized_phone="+14693249654", status="active", version=0,
                        created_at=NOW, updated_at=NOW, campaign_name="Cold Lead"))
        s.flush()
        yield s


@pytest.mark.parametrize("handler", [ia._handle_do_not_call, ia._handle_not_interested, ia._handle_wrong_number])
def test_the_opt_out_handlers_pass_the_session_so_the_live_flags_are_used(session, handler):
    with patch.object(ia, "_write_ghl_campaign_off") as w:
        handler(session, "+14693249654", "+14693249654", {}, SETTINGS)
    assert w.call_count == 1 and w.call_args.args[3] is session


def test_the_wrapper_passes_the_dashboard_flags_and_never_raises(session):
    with patch("app.adapters.ghl.GHLClient", return_value="client"), patch("app.core.mode_flags.get_mode_flags", return_value=LIVE),             patch.object(ia, "set_ai_campaign_no", return_value=True) as w:
        assert ia._write_ghl_campaign_off("+14693249654", SETTINGS, "do_not_call", session) is True
    assert w.call_args.args == ("client", "+14693249654", SETTINGS, LIVE, "do_not_call")                # the live flags travel with the write
    with patch("app.adapters.ghl.GHLClient", side_effect=RuntimeError("GHL down")):
        assert ia._write_ghl_campaign_off("+14693249654", SETTINGS, "do_not_call", session) is False   # failure is reported, never raised
    with patch("app.adapters.ghl.GHLClient", return_value="client"), patch.object(ia, "set_ai_campaign_no", return_value=True) as w2:
        ia._write_ghl_campaign_off("+14693249654", SETTINGS, "do_not_call")                             # no session: no flags (old call shape)
    assert w2.call_args.args[3] is None


# -- the backfill script ---------------------------------------------------------------------------------------------------------
_spec = importlib.util.spec_from_file_location("bf", Path(__file__).resolve().parents[2] / "execution" / "backfill_ai_campaign_off.py")
BF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BF)


def seed(session):
    for cid, dnc, inv in (("+15550000001", True, False), ("+15550000002", False, True), ("+15550000003", True, False), ("+15550000004", True, False),
                          ("+15550000005", False, False)):
        session.add(LeadState(id=str(uuid.uuid4()), contact_id=cid, normalized_phone=cid, status="closed", version=0, created_at=NOW, updated_at=NOW,
                              do_not_call=dnc, invalid=inv))
    session.flush()


def ghl_for_backfill():
    return FakeGHL([contact("A", "+15550000001", "Yes"), contact("B", "+15550000002", "Yes"), contact("C", "+15550000003", "No"),
                    contact("Z", "+15550000005", "Yes")])          # ...0004 has no GHL contact; ...0005 is not opted out


def test_backfill_dry_run_lists_what_it_would_fix_and_writes_nothing(session):
    seed(session)
    ghl, lines = ghl_for_backfill(), []
    res = BF.run(session, SETTINGS, ghl, LIVE, False, None, lines.append)
    assert res["leads"] == 4 and res["to_fix"] == 2 and res["unmatched"] == 1 and res["written"] == 0
    assert ghl.updates == [] and "DRY RUN" in lines[-1]
    assert any("+15550000001" in ln for ln in lines) and not any("+15550000003" in ln for ln in lines)     # the already-No one is not listed


def test_backfill_apply_writes_only_the_needed_contacts_and_verifies_each_by_reading_back(session):
    seed(session)
    ghl = ghl_for_backfill()
    res = BF.run(session, SETTINGS, ghl, LIVE, True, None, lambda *_: None)
    assert res["written"] == 2 and res["verified"] == 2 and res["failed"] == 0
    assert sorted(u[0] for u in ghl.updates) == ["A", "B"]                  # C was already No, 0004 unmatched, Z is not opted out
    assert BF.field_value(ghl.get_contact("Z")["contact"], FIELD) == "Yes"  # a lead that never opted out is untouched


def test_backfill_is_idempotent(session):
    seed(session)
    ghl = ghl_for_backfill()
    BF.run(session, SETTINGS, ghl, LIVE, True, None, lambda *_: None)
    again = BF.run(session, SETTINGS, ghl, LIVE, True, None, lambda *_: None)
    assert again["to_fix"] == 0 and again["written"] == 0 and len(ghl.updates) == 2


def test_backfill_in_shadow_mode_reports_failures_instead_of_pretending(session):
    seed(session)
    ghl, lines = ghl_for_backfill(), []
    res = BF.run(session, SETTINGS, ghl, SHADOW, True, None, lines.append)
    assert res["written"] == 0 and res["failed"] == 2 and ghl.updates == [] and any("NOT WRITTEN" in ln for ln in lines)


def test_backfill_field_value_reader():
    assert BF.field_value({"customFields": [{"id": FIELD, "value": ["No"]}]}, FIELD) == "No"
    assert BF.field_value({"customFields": [{"id": FIELD, "value": "Yes"}]}, FIELD) == "Yes"
    assert BF.field_value({"customFields": []}, FIELD) == "None"
