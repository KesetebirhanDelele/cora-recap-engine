"""Spec/49 step 5 — orchestration with a mocked GHL client and in-memory SQLite (no network)."""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun
from app.models.staff_call_quality import StaffCallQuality
from app.services.explorer_queue import run_explorer_queue_cycle
from app.services.explorer_queue_feed import FixtureFeed

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "explorer_queue" / "feed_reach_out.json"
T0 = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)
AI_FIELD = "AIFIELD"


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine, tables=[ExplorerQueueRun.__table__, ExplorerQueueContact.__table__, StaffCallQuality.__table__]
    )
    with sessionmaker(bind=engine)() as s:
        yield s


def settings(**kw):
    d = dict(
        ghl_location_id="LOCA", explorer_queue_view="reach_out", explorer_queue_batch_cap=200,
        explorer_queue_score_field_id="SCOREF", explorer_queue_ai_summary_field_id="SUMF",
        explorer_queue_ai_campaign_field_id=AI_FIELD,
        explorer_queue_stage_map='{"new_explorer": "ent-stage-new-explorer", "activating": "ent-stage-activating"}',
        explorer_queue_feed_path="",
    )
    d.update(kw)
    return SimpleNamespace(**d)


def flags(enabled=True, writes=True):
    return SimpleNamespace(explorer_queue_tagging_enabled=enabled, ghl_writes_enabled=writes)


def run(session, mode="apply", fl=None, client=None, lookup=None, now=T0, st=None):
    client = client or MagicMock()
    lookup = lookup or (lambda cid: {"contact": {"tags": [], "customFields": []}})
    with patch("app.core.mode_flags.get_mode_flags", return_value=fl or flags()):
        r = run_explorer_queue_cycle(
            session, st or settings(), mode=mode, feed=FixtureFeed(FIXTURE),
            client=client, contact_lookup=lookup, now=now,
        )
    session.commit()
    return r, client


def add_call(session, cid, when, connected):
    session.add(StaffCallQuality(
        id=str(uuid.uuid4()), ghl_message_id=str(uuid.uuid4()), ghl_contact_id=cid,
        call_time=when, call_connected=connected,
    ))
    session.commit()


def test_apply_with_flag_off_is_skipped_and_touches_nothing(session):
    r, c = run(session, fl=flags(enabled=False))
    assert r.status == "skipped"
    assert not c.method_calls and session.scalars(select(ExplorerQueueContact)).all() == []


def test_dry_run_ignores_flag_and_writes_nothing(session):
    r, c = run(session, mode="dry", fl=flags(enabled=False))
    assert r.status == "completed" and r.dry_run is True
    assert r.contacts_tagged == 4 and r.skipped_no_ghl_url == 1 and r.skipped_no_access == 1
    c.add_contact_tag.assert_not_called()
    c.update_contact_fields.assert_not_called()
    assert session.scalars(select(ExplorerQueueContact)).all() == []


def test_no_feed_configured_is_skipped(session):
    with patch("app.core.mode_flags.get_mode_flags", return_value=flags()):
        r = run_explorer_queue_cycle(session, settings(), mode="apply", client=MagicMock())
    assert r.status == "skipped" and "feed" in r.error_message


def test_apply_tags_writes_score_and_saves_state(session):
    r, c = run(session)
    assert r.status == "completed" and r.dry_run is False
    assert (r.contacts_tagged, r.skipped_no_ghl_url, r.skipped_no_access) == (4, 1, 1)
    added = {(a.args[0], a.args[1]) for a in c.add_contact_tag.call_args_list}
    assert ("CIDONE", "ent-priority-call") in added and ("CIDONE", "ent-score-high") in added
    assert ("CIDONE", "ent-stage-new-explorer") in added
    assert not any(cid in ("CIDFIVE",) for cid, _ in added)  # other sub-account never written
    fields = {a.args[0]: a.args[1] for a in c.update_contact_fields.call_args_list}
    assert fields["CIDONE"]["SCOREF"] == 52 and "SUMF" in fields["CIDONE"] and "SUMF" not in fields["CIDTWO"]
    assert len(session.scalars(select(ExplorerQueueContact)).all()) == 4


def test_second_run_makes_zero_writes(session):
    run(session)
    state_tags = {
        "CIDONE": ["ent-priority-call", "ent-src-campus-ranking", "ent-stage-new-explorer", "ent-score-high"],
        "CIDTWO": ["ent-priority-call", "ent-src-campus-ranking", "ent-stage-activating", "ent-score-mid"],
        "CIDTHREE": ["ent-priority-call", "ent-src-campus-ranking", "ent-score-low"],
        "CIDSIX": ["ent-priority-call", "ent-src-campus-ranking", "ent-stage-activating", "ent-score-high"],
    }
    r, c = run(session, lookup=lambda cid: {"contact": {"tags": state_tags[cid], "customFields": []}}, now=T0 + timedelta(days=1))
    assert r.contacts_tagged == 0 and r.contacts_already_tagged == 4
    c.add_contact_tag.assert_not_called()
    c.remove_contact_tag.assert_not_called()
    c.update_contact_fields.assert_not_called()


def test_ai_campaign_contact_is_skipped(session):
    def lookup(cid):
        cf = [{"id": AI_FIELD, "value": ["Yes"]}] if cid == "CIDTWO" else []
        return {"contact": {"tags": [], "customFields": cf}}
    r, c = run(session, lookup=lookup)
    assert r.skipped_ai_campaign == 1 and r.contacts_tagged == 3
    assert all(a.args[0] != "CIDTWO" for a in c.add_contact_tag.call_args_list)


def test_connected_call_after_tag_drains_and_is_not_retagged(session):
    run(session)
    add_call(session, "CIDONE", T0 + timedelta(hours=6), connected=True)
    add_call(session, "CIDTWO", T0 + timedelta(hours=6), connected=False)  # no-answer: stays
    tags = ["ent-priority-call", "ent-src-campus-ranking"]
    r, c = run(session, lookup=lambda cid: {"contact": {"tags": tags}}, now=T0 + timedelta(days=1))
    removed = [(a.args[0], a.args[1]) for a in c.remove_contact_tag.call_args_list if a.args[1] == "ent-priority-call"]
    assert removed == [("CIDONE", "ent-priority-call")]
    assert r.contacts_untagged == 1
    st = session.get(ExplorerQueueContact, "CIDONE")
    assert st.drained_at is not None and session.get(ExplorerQueueContact, "CIDTWO").drained_at is None
    # third night: drained contact is skipped, never re-tagged
    r3, c3 = run(session, lookup=lambda cid: {"contact": {"tags": tags}}, now=T0 + timedelta(days=2))
    assert r3.contacts_untagged == 0
    assert all(a.args[0] != "CIDONE" for a in c3.add_contact_tag.call_args_list)


def test_one_failing_write_does_not_stop_the_batch(session):
    c = MagicMock()
    c.add_contact_tag.side_effect = lambda cid, tag, **kw: (_ for _ in ()).throw(RuntimeError("boom")) if cid == "CIDTWO" else {}
    r, _ = run(session, client=c)
    assert r.status == "completed" and r.contacts_failed == 1
    assert session.get(ExplorerQueueContact, "CIDTWO") is None
    assert session.get(ExplorerQueueContact, "CIDONE") is not None


def test_shadow_ghl_mode_records_dry_run_and_no_state(session):
    r, _ = run(session, fl=flags(enabled=True, writes=False))
    assert r.dry_run is True
    assert session.scalars(select(ExplorerQueueContact)).all() == []
