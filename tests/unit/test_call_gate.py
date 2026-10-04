"""Opt-out call gate (spec/39): GHL do-not-disturb, Call DND and opt-out tags stop a call; GHL unreadable fails closed."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core import call_gate as cg
from app.models import Base, ScheduledJob
from app.models.audit import AuditLog
from app.models.lead_state import LeadState
from app.services import call_gate as svc

pytestmark = pytest.mark.real_call_gate

TAGS = cg.parse_tags(None)


# ───────────────────────── pure rules ─────────────────────────

def test_contact_level_dnd_blocks():
    assert cg.blocking_reason({"contact": {"dnd": True}}, TAGS) == ("GHL do-not-disturb is on for all channels", True)


@pytest.mark.parametrize("status", ["active", "ACTIVE", "permanent"])
def test_call_dnd_blocks(status):
    rec = {"contact": {"dndSettings": {"Call": {"status": status}}}}
    assert cg.blocking_reason(rec, TAGS) == ("GHL Call do-not-disturb is active", True)


def test_dnd_on_another_channel_does_not_block_a_call():
    rec = {"contact": {"dndSettings": {"SMS": {"status": "active"}, "Email": {"status": "active"}, "Call": {"status": "inactive"}}}}
    assert cg.blocking_reason(rec, TAGS) is None


@pytest.mark.parametrize("tag,strong", [("do not contact", True), ("Do Not Call Again", True), ("dnd", True), ("not interested", False)])
def test_opt_out_tags_block(tag, strong):
    reason, is_strong = cg.blocking_reason({"contact": {"tags": ["ai cold leads", tag]}}, TAGS)
    assert tag.lower() in reason.lower() and is_strong is strong


def test_ordinary_tags_and_empty_records_do_not_block():
    assert cg.blocking_reason({"contact": {"tags": ["left voicemail", "ai cold leads", "no email"]}}, TAGS) is None
    assert cg.blocking_reason({}, TAGS) is None
    assert cg.blocking_reason(None, TAGS) is None


def test_block_tag_list_is_configurable():
    assert cg.parse_tags("alpha, Beta ,,") == frozenset({"alpha", "beta"})
    assert cg.blocking_reason({"contact": {"tags": ["not interested"]}}, cg.parse_tags("alpha")) is None
    assert cg.blocking_reason({"contact": {"tags": ["alpha"]}}, cg.parse_tags("alpha")) is not None


# ───────────────────────── service ─────────────────────────

class FakeGHL:
    def __init__(self, record=None, found=True, error=None):
        self.record, self.found, self.error = record or {}, found, error

    def search_contact_by_phone(self, phone):
        if self.error:
            raise self.error
        return {"id": "ghl1"} if self.found else None

    def get_contact(self, cid):
        if self.error:
            raise self.error
        return {"contact": self.record}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


SETTINGS = SimpleNamespace()


def _check(session, ghl, contact="+15550001111", job="job-1"):
    return svc.check(session, SETTINGS, contact, job_id=job, ghl=ghl)


def test_allow_when_ghl_says_nothing(session):
    assert _check(session, FakeGHL({"tags": ["left voicemail"]})).action == "allow"


def test_allow_when_ghl_has_no_such_contact(session):
    assert _check(session, FakeGHL(found=False)).action == "allow"


def test_block_on_tag_and_dnd(session):
    r = _check(session, FakeGHL({"tags": ["do not contact"]}))
    assert (r.action, r.strong) == ("block", True) and "do not contact" in r.reason
    assert _check(session, FakeGHL({"dndSettings": {"Call": {"status": "active"}}})).action == "block"


def test_ghl_outage_fails_closed_then_gives_up_after_six_attempts(session):
    ghl = FakeGHL(error=RuntimeError("GHL HTTP error: 502"))
    actions = [_check(session, ghl, job="job-x").action for _ in range(6)]
    assert actions == ["defer"] * 5 + ["give_up"]
    rows = session.scalars(select(AuditLog).where(AuditLog.action == "call_gate_lookup_failed", AuditLog.entity_id == "job-x")).all()
    assert len(rows) == 6
    assert _check(session, ghl, job="another-job").action == "defer"          # attempts are counted per job


def test_apply_block_cancels_jobs_and_marks_the_lead(session):
    now = datetime.now(tz=timezone.utc)
    contact = "+15550002222"
    session.add(LeadState(id=str(uuid.uuid4()), contact_id=contact, normalized_phone=contact, status="cold", version=0,
                          created_at=now, updated_at=now))
    jobs = []
    for _ in range(2):
        j = ScheduledJob(id=str(uuid.uuid4()), job_type="launch_outbound_call", entity_type="lead", entity_id=contact, run_at=now,
                         status="pending", payload_json={"contact_id": contact}, version=0, created_at=now, updated_at=now)
        session.add(j)
        jobs.append(j)
    session.flush()

    svc.apply_block(session, jobs[0], contact, svc.GateResult("block", "GHL tag 'do not contact'", True))
    session.commit()

    assert {session.get(ScheduledJob, j.id).status for j in jobs} == {"cancelled"}
    lead = session.scalars(select(LeadState).where(LeadState.contact_id == contact)).one()
    assert lead.do_not_call is True and lead.status == "closed"
    assert session.scalars(select(AuditLog).where(AuditLog.action == "call_blocked_ghl_optout")).one().entity_id == contact


def test_not_interested_closes_the_lead_without_setting_do_not_call(session):
    now = datetime.now(tz=timezone.utc)
    contact = "+15550003333"
    session.add(LeadState(id=str(uuid.uuid4()), contact_id=contact, normalized_phone=contact, status="cold", version=0,
                          created_at=now, updated_at=now))
    job = ScheduledJob(id=str(uuid.uuid4()), job_type="launch_outbound_call", entity_type="lead", entity_id=contact, run_at=now,
                       status="pending", payload_json={"contact_id": contact}, version=0, created_at=now, updated_at=now)
    session.add(job)
    session.flush()
    svc.apply_block(session, job, contact, svc.GateResult("block", "GHL tag 'not interested'", False))
    session.commit()
    lead = session.scalars(select(LeadState).where(LeadState.contact_id == contact)).one()
    assert not lead.do_not_call and lead.status == "closed"


def test_gate_can_be_switched_off_in_config(session):
    assert svc.enabled(session, SimpleNamespace()) is True
    assert svc.enabled(session, SimpleNamespace(call_gate_enabled="false")) is False
