"""Spec/49 dashboard tile data — counts and the 'called at least once' list."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun
from app.models.staff_call_quality import StaffCallQuality
from app.services.dashboard_metrics import get_explorer_queue_summary

T0 = datetime(2026, 11, 4, 12, 0)


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        ExplorerQueueRun.__table__, ExplorerQueueContact.__table__, StaffCallQuality.__table__])
    return sessionmaker(bind=engine)()


def _contact(s, cid, score, drained=False, reason="connected_call", hold=False):
    s.add(ExplorerQueueContact(ghl_contact_id=cid, ghl_location_id="L", tagged_at=T0, last_score=score,
                               drained_at=T0 + timedelta(days=1) if drained else None,
                               exit_reason=reason if drained else None, callback_hold=hold))


def _call(s, cid, when, connected=False):
    s.add(StaffCallQuality(id=str(uuid.uuid4()), ghl_message_id=str(uuid.uuid4()), ghl_contact_id=cid,
                           call_time=when, call_connected=connected))


def test_empty_tables_return_zeros():
    out = get_explorer_queue_summary(_session())
    assert out["queue"] == {"in_queue": 0, "never_called": 0, "called_not_reached": 0,
                            "callback_pending": 0, "exited": 0}
    assert out["exits"] == {}
    assert out["attempted"] == [] and out["runs"] == []


def test_counts_and_attempted_list():
    s = _session()
    _contact(s, "never", 50)
    _contact(s, "tried_twice", 30)
    _contact(s, "tried_once", 60)
    _contact(s, "done", 40, drained=True)
    _contact(s, "no", 10, drained=True, reason="not_interested")
    _contact(s, "cb", 20, hold=True)
    _call(s, "tried_twice", T0 + timedelta(hours=1))
    _call(s, "tried_twice", T0 + timedelta(hours=2))
    _call(s, "tried_once", T0 + timedelta(hours=3))
    _call(s, "never", T0 - timedelta(days=3))          # before the tag: does not count
    _call(s, "done", T0 + timedelta(hours=1), True)
    s.commit()
    out = get_explorer_queue_summary(s)
    assert out["queue"] == {"in_queue": 4, "never_called": 2, "called_not_reached": 2,
                            "callback_pending": 1, "exited": 2}
    assert out["exits"] == {"connected_call": 1, "not_interested": 1}
    assert [(a["ghl_contact_id"], a["attempts"]) for a in out["attempted"]] == [("tried_twice", 2), ("tried_once", 1)]
