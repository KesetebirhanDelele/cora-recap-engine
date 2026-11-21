"""Daily 'who staff reached' push to Ali (spec/49 addendum, 2026-11-18). No network, no real token."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.app_config import set_config_value
from app.models.app_config import AppConfig
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.staff_call_quality import StaffCallQuality
from app.services import explorer_contact_activity as eca

URL = "https://api.example.test/api/v1/explorer-growth/contact-activity"
TOKEN = "TESTTOKEN-not-real"
NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
OK = {"source": "ghl-rose-calls", "received": 2, "matched": 2, "unmatched": 0, "written": 2, "unchanged": 0,
      "rejected_future": 0, "unmatched_ghl_contact_ids": []}


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[StaffCallQuality.__table__, AppConfig.__table__, AuditLog.__table__])
    with sessionmaker(bind=engine)() as s:
        yield s


_n = 0


def call(session, cid, when, connected=True, rep="R1", human=True, direction="outbound"):
    global _n
    _n += 1
    session.add(StaffCallQuality(
        id=f"id{_n}", ghl_message_id=f"m{_n}", ghl_contact_id=cid, rep_user_id=rep, call_time=when,
        call_connected=connected, direction=direction, answered_by_human=human))
    session.commit()


def settings(**kw):
    return SimpleNamespace(explorer_contact_activity_url=URL, cora_service_token=TOKEN, **kw)


def client_with(handler):
    return eca.ContactActivityClient(
        URL, TOKEN, client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


# ── file contents ────────────────────────────────────────────────────────────────

def test_one_row_per_contact_with_latest_connected_call_in_utc_z(session):
    call(session, "A", NOW - timedelta(days=3))
    call(session, "A", NOW - timedelta(days=1, hours=2))   # later call for A wins
    call(session, "B", NOW - timedelta(days=5), rep="R2")  # another staff member still counts
    rows = eca.build_rows(session, NOW - timedelta(days=14), NOW)
    assert rows == [
        {"ghl_contact_id": "A", "last_connected_call_at": "2026-11-17T10:00:00Z"},
        {"ghl_contact_id": "B", "last_connected_call_at": "2026-11-13T12:00:00Z"},
    ]


def test_only_the_configured_staff_member_counts(session):
    call(session, "ROSE", NOW - timedelta(days=2), rep="R1")
    call(session, "OTHER", NOW - timedelta(days=2), rep="R2")
    call(session, "NOREP", NOW - timedelta(days=2), rep=None)
    rep = eca.rep_ids(SimpleNamespace(explorer_contact_activity_rep_user_ids=" R1 "))
    assert rep == ("R1",)
    rows = eca.build_rows(session, NOW - timedelta(days=14), NOW, rep)
    assert [r["ghl_contact_id"] for r in rows] == ["ROSE"]
    assert eca.rep_ids(SimpleNamespace(explorer_contact_activity_rep_user_ids="")) == ()  # empty = all staff
    assert len(eca.build_rows(session, NOW - timedelta(days=14), NOW)) == 3


def test_real_settings_default_is_rose_and_taiwo():
    from app.config.settings import Settings

    assert eca.rep_ids(Settings()) == ("0swBv9tBNeXeYXPYFBSx", "93bhNRgb5pzSoHmaSimH")   # Rose + Taiwo (Kes 2026-11-20)


def test_voicemail_unjudged_and_inbound_calls_are_never_sent(session):
    call(session, "VM", NOW - timedelta(days=1), human=False)          # left a voicemail (judge says no live person)
    call(session, "UNJUDGED", NOW - timedelta(days=1), human=None)     # not judged yet / judge failed
    call(session, "INBOUND", NOW - timedelta(days=1), direction="inbound")  # lead called in, not staff reaching them
    call(session, "NODIR", NOW - timedelta(days=1), direction=None)
    call(session, "REAL", NOW - timedelta(days=1))
    assert [r["ghl_contact_id"] for r in eca.build_rows(session, NOW - timedelta(days=14), NOW)] == ["REAL"]


def test_a_voicemail_after_a_real_conversation_does_not_move_the_time(session):
    call(session, "A", NOW - timedelta(days=3))                  # real conversation
    call(session, "A", NOW - timedelta(days=1), human=False)     # later voicemail must not count
    rows = eca.build_rows(session, NOW - timedelta(days=14), NOW)
    assert rows == [{"ghl_contact_id": "A", "last_connected_call_at": "2026-11-15T12:00:00Z"}]


def test_no_answer_is_never_sent_and_blank_ids_are_dropped(session):
    call(session, "NOANS", NOW - timedelta(days=1), connected=False)
    call(session, "", NOW - timedelta(days=1))
    call(session, "OK", NOW - timedelta(days=1))
    assert [r["ghl_contact_id"] for r in eca.build_rows(session, NOW - timedelta(days=14), NOW)] == ["OK"]


def test_calls_outside_the_window_are_left_out(session):
    call(session, "OLD", NOW - timedelta(days=20))
    call(session, "FUTURE", NOW + timedelta(hours=1))
    assert eca.build_rows(session, NOW - timedelta(days=14), NOW) == []


def test_naive_and_non_utc_times_are_written_as_utc():
    assert eca.iso_z(datetime(2026, 11, 17, 21, 4, 0)) == "2026-11-17T21:04:00Z"
    cst = timezone(timedelta(hours=-6))
    assert eca.iso_z(datetime(2026, 11, 17, 15, 4, 0, tzinfo=cst)) == "2026-11-17T21:04:00Z"


# ── window ───────────────────────────────────────────────────────────────────────

def test_first_push_is_14_days_then_every_run_resends_the_trailing_8_days():
    assert eca.window_start(None, NOW) == NOW - timedelta(days=14)
    assert eca.window_start(NOW - timedelta(days=1), NOW) == NOW - timedelta(days=8)


def test_after_an_outage_the_window_reaches_back_to_the_last_success_but_never_past_14_days():
    assert eca.window_start(NOW - timedelta(days=10), NOW) == NOW - timedelta(days=11)
    assert eca.window_start(NOW - timedelta(days=90), NOW) == NOW - timedelta(days=14)


def test_operator_override_days_wins():
    assert eca.window_start(NOW - timedelta(days=1), NOW, 14) == NOW - timedelta(days=14)


# ── sending ──────────────────────────────────────────────────────────────────────

def test_dry_run_sends_and_records_nothing(session):
    call(session, "A", NOW - timedelta(days=1))
    res = eca.push_contact_activity(session, settings(), now=NOW, dry_run=True)
    assert res.rows == 1 and res.sent is False and res.skipped_reason == "dry_run"
    assert session.get(AppConfig, eca.LAST_OK_KEY) is None


def test_push_sends_bearer_body_and_records_success(session):
    call(session, "A", NOW - timedelta(days=1))
    call(session, "B", NOW - timedelta(days=2))
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=OK)

    res = eca.push_contact_activity(session, settings(), now=NOW, client=client_with(handler))
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert set(seen["body"]) == {"source", "rows"} and seen["body"]["source"] == "ghl-rose-calls"
    assert all(set(r) == {"ghl_contact_id", "last_connected_call_at"} for r in seen["body"]["rows"])
    assert all(r["last_connected_call_at"].endswith("Z") for r in seen["body"]["rows"])
    assert res.sent and res.matched == 2 and res.written == 2
    assert session.get(AppConfig, eca.LAST_OK_KEY).value == eca.iso_z(NOW)


def test_a_call_six_days_old_is_resent_so_a_late_joiner_still_gets_it(session):
    call(session, "A", NOW - timedelta(days=6))
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=OK)

    eca.push_contact_activity(session, settings(), now=NOW, client=client_with(handler))
    eca.push_contact_activity(session, settings(), now=NOW + timedelta(days=1), client=client_with(handler))
    assert [len(b["rows"]) for b in bodies] == [1, 1]            # re-sent the next day
    eca.push_contact_activity(session, settings(), now=NOW + timedelta(days=3), client=client_with(handler))
    assert len(bodies) == 2                                        # 9 days old: out of the window, nothing to send


def test_unmatched_ids_are_collected_for_the_operator(session):
    call(session, "A", NOW - timedelta(days=1))
    out = {**OK, "unmatched": 1, "unmatched_ghl_contact_ids": ["ZZZ"]}
    res = eca.push_contact_activity(session, settings(), now=NOW, client=client_with(lambda r: httpx.Response(200, json=out)))
    assert res.unmatched_ids == ["ZZZ"]


def test_second_run_resends_inside_the_window(session):
    call(session, "A", NOW - timedelta(days=1))
    eca.push_contact_activity(session, settings(), now=NOW, client=client_with(lambda r: httpx.Response(200, json=OK)))
    later = NOW + timedelta(days=1)
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json=OK)

    eca.push_contact_activity(session, settings(), now=later, client=client_with(handler))
    assert [r["ghl_contact_id"] for r in bodies[0]["rows"]] == ["A"]  # inside the 8-day window -> re-sent, harmless


def test_large_file_goes_in_batches_and_counts_add_up(session):
    for i in range(eca.BATCH_SIZE + 3):
        call(session, f"C{i:04d}", NOW - timedelta(hours=i % 20 + 1))
    sizes = []

    def handler(req):
        n = len(json.loads(req.content)["rows"])
        sizes.append(n)
        return httpx.Response(200, json={**OK, "received": n, "matched": n, "written": n})

    res = eca.push_contact_activity(session, settings(), now=NOW, client=client_with(handler))
    assert sizes == [eca.BATCH_SIZE, 3] and res.batches == 2 and res.matched == eca.BATCH_SIZE + 3


def test_nothing_to_send_is_a_success_that_advances_the_mark(session):
    res = eca.push_contact_activity(session, settings(), now=NOW, client=client_with(lambda r: 1 / 0))
    assert res.rows == 0 and res.sent is False and res.skipped_reason == "no_rows"
    assert session.get(AppConfig, eca.LAST_OK_KEY).value == eca.iso_z(NOW)


# ── failure paths: loud, never mark success ──────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 403, 404, 302])
def test_rejections_and_redirects_fail_once_and_do_not_mark_success(session, status):
    call(session, "A", NOW - timedelta(days=1))
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, text="no")

    with pytest.raises(eca.ContactActivityError) as ei:
        eca.push_contact_activity(session, settings(), now=NOW, client=client_with(handler))
    assert len(calls) == 1
    assert TOKEN not in str(ei.value)
    assert session.get(AppConfig, eca.LAST_OK_KEY) is None


def test_server_errors_retry_then_fail_without_marking_success(session):
    call(session, "A", NOW - timedelta(days=1))
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(eca.ContactActivityError):
        eca.push_contact_activity(session, settings(), now=NOW, client=client_with(handler))
    assert len(calls) == eca.RETRIES + 1
    assert session.get(AppConfig, eca.LAST_OK_KEY) is None


def test_a_transient_error_then_success_recovers(session):
    call(session, "A", NOW - timedelta(days=1))
    seq = iter([httpx.Response(502), httpx.Response(200, json=OK)])
    res = eca.push_contact_activity(session, settings(), now=NOW, client=client_with(lambda r: next(seq)))
    assert res.sent


def test_a_failed_second_batch_leaves_the_old_mark_so_the_next_run_resends(session):
    for i in range(eca.BATCH_SIZE + 1):
        call(session, f"C{i:04d}", NOW - timedelta(hours=1))
    seq = iter([httpx.Response(200, json=OK), httpx.Response(400, text="bad")])
    with pytest.raises(eca.ContactActivityError):
        eca.push_contact_activity(session, settings(), now=NOW, client=client_with(lambda r: next(seq)))
    assert session.get(AppConfig, eca.LAST_OK_KEY) is None


def test_response_without_the_counts_is_an_error(session):
    call(session, "A", NOW - timedelta(days=1))
    with pytest.raises(eca.ContactActivityError):
        eca.push_contact_activity(
            session, settings(), now=NOW, client=client_with(lambda r: httpx.Response(200, json={"ok": True})))


def test_requires_url_token_and_https():
    with pytest.raises(eca.ContactActivityNotConfigured):
        eca.ContactActivityClient("", TOKEN)
    with pytest.raises(eca.ContactActivityNotConfigured):
        eca.ContactActivityClient(URL, "")
    with pytest.raises(eca.ContactActivityNotConfigured):
        eca.ContactActivityClient("http://api.example.test/x", TOKEN)
    with pytest.raises(eca.ContactActivityNotConfigured):  # a tracker-style, schemeless value is refused too
        eca.ContactActivityClient("enterprise.colaberry.ai/api/v1/explorer-growth/contact-activity", TOKEN)


def test_rejected_future_is_counted_not_fatal(session):
    call(session, "A", NOW - timedelta(days=1))
    res = eca.push_contact_activity(
        session, settings(), now=NOW,
        client=client_with(lambda r: httpx.Response(200, json={**OK, "rejected_future": 1, "written": 0})))
    assert res.sent and res.rejected_future == 1


# ── switch ───────────────────────────────────────────────────────────────────────

def test_off_by_default_and_on_when_flag_set(session):
    assert eca.enabled(session, settings()) is False
    set_config_value(session, eca.ENABLED_KEY, "true", updated_by="test")
    session.commit()
    assert eca.enabled(session, settings()) is True


# ── worker ordering: push first; a failed push skips tagging ──────────────────────

def _run_job(monkeypatch, *, enabled, push_raises, judge_raises=False):
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    import app.db as db
    import app.services.explorer_queue as queue
    import app.worker.claim as claim
    import app.worker.exceptions as exc_mod
    import app.worker.jobs.explorer_queue_jobs as jobs

    order, created = [], []
    sess = MagicMock()

    @contextmanager
    def fake_session():
        yield sess

    monkeypatch.setattr(db, "get_sync_session", fake_session)
    monkeypatch.setattr(claim, "claim_job", lambda *a, **k: SimpleNamespace(id="j1"))
    for name in ("mark_running", "complete_job", "fail_job"):
        monkeypatch.setattr(claim, name, lambda *a, **k: None)
    monkeypatch.setattr(claim, "get_worker_id", lambda: "w")
    monkeypatch.setattr(exc_mod, "create_exception", lambda *a, **k: created.append(k["type"]))
    monkeypatch.setattr(jobs, "_reschedule", lambda s: None)
    monkeypatch.setattr(eca, "enabled", lambda s, st: enabled)

    def fake_push(*a, **k):
        order.append("push")
        if push_raises:
            raise eca.ContactActivityError("boom")

    monkeypatch.setattr(eca, "push_contact_activity", fake_push)

    import app.core.human_answer_judge as hj

    def fake_judge(*a, **k):
        order.append("judge")
        if judge_raises:
            raise RuntimeError("openai down")
        return {"judged": 0, "human": 0, "not_human": 0, "failed": 0}

    monkeypatch.setattr(hj, "judge_pending", fake_judge)
    monkeypatch.setattr(queue, "run_explorer_queue_cycle", lambda *a, **k: order.append("tag"))
    jobs.explorer_queue_sync_job("j1")
    return order, created


def test_job_pushes_before_tagging(monkeypatch):
    order, created = _run_job(monkeypatch, enabled=True, push_raises=False)
    assert order == ["judge", "push", "tag"] and created == []


def test_job_skips_tagging_and_raises_exception_when_push_fails(monkeypatch):
    order, created = _run_job(monkeypatch, enabled=True, push_raises=True)
    assert order == ["judge", "push"] and created == ["explorer_contact_activity_failed"]


def test_job_does_not_push_when_switched_off(monkeypatch):
    order, created = _run_job(monkeypatch, enabled=False, push_raises=False)
    assert order == ["judge", "tag"] and created == []


def test_a_failing_judge_never_stops_the_job(monkeypatch):
    order, created = _run_job(monkeypatch, enabled=True, push_raises=False, judge_raises=True)
    assert order == ["judge", "push", "tag"] and created == []
