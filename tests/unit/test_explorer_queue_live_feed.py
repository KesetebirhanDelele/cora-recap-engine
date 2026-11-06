"""Live feed reader (Ali, 2026-11-06) and the skipped-lead report — no network, no real token."""
from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.explorer_queue import ExplorerQueueContact, ExplorerQueueRun, ExplorerQueueSkip
from app.models.staff_call_quality import StaffCallQuality
from app.services.dashboard_metrics import get_explorer_queue_summary
from app.services.explorer_queue import run_explorer_queue_cycle
from app.services.explorer_queue_feed import (
    FeedError,
    FeedNotConfigured,
    FixtureFeed,
    LiveFeed,
    build_feed,
)

URL = "https://feed.example.test/api/v1/explorer-growth/outreach"
TOKEN = "TESTTOKEN-not-real"
LOC = "LOCA"
GOOD = f"https://app.gohighlevel.com/v2/location/{LOC}/contacts/detail/CID"
OTHER = "https://app.gohighlevel.com/v2/location/OTHERLOC/contacts/detail/CIDX"


def payload(n=3, **over):
    rows = [{"enrollment_id": f"e{i}", "lead_id": f"l{i}", "primary_state": "NOT_STARTED",
             "score": 50 - i, "reasons": ["Active today"], "ghl_url": f"{GOOD}{i}"} for i in range(1, n + 1)]
    d = {"view": "reach_out", "tracked_since": "2026-10-26", "count": len(rows), "rows": rows}
    d.update(over)
    return d


def feed_with(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LiveFeed(URL, TOKEN, client=client, sleep=lambda s: None, **kw)


def test_sends_only_view_and_limit_and_a_bearer_token():
    seen = {}

    def handler(req: httpx.Request):
        seen["params"] = dict(req.url.params)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json=payload())

    res = feed_with(handler).fetch("reach_out", 200)
    assert seen["params"] == {"view": "reach_out", "limit": "200"}  # nothing else: extra params are a 400
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert len(res.rows) == 3 and res.count == 3 and res.rows[0].rank == 1 and res.rows[1].lead_id == "l2"


def test_live_rows_without_name_or_email_parse():
    res = feed_with(lambda r: httpx.Response(200, json=payload())).fetch("reach_out", 200)
    assert res.rows[0].name is None and res.rows[0].why_now == "Active today"


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_credential_fails_loudly_without_retry_or_token_in_message(status):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, json={"error": "Invalid service token"})

    with pytest.raises(FeedError) as e:
        feed_with(handler).fetch("reach_out", 200)
    assert len(calls) == 1 and TOKEN not in str(e.value)


def test_400_is_not_retried():
    calls = []
    with pytest.raises(FeedError, match="parameters"):
        feed_with(lambda r: (calls.append(1), httpx.Response(400))[1]).fetch("reach_out", 200)
    assert len(calls) == 1


def test_5xx_is_retried_then_succeeds_and_is_bounded_when_it_keeps_failing():
    n = {"c": 0}

    def flaky(req):
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, json=payload())

    assert len(feed_with(flaky).fetch("reach_out", 200).rows) == 3 and n["c"] == 3
    n["c"] = 0
    with pytest.raises(FeedError, match="server error"):
        feed_with(lambda r: (n.__setitem__("c", n["c"] + 1), httpx.Response(500))[1]).fetch("reach_out", 200)
    assert n["c"] == LiveFeed.RETRIES + 1


def test_network_error_is_retried_then_raises():
    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(FeedError, match="network error"):
        feed_with(boom).fetch("reach_out", 200)


def test_count_mismatch_and_wrong_view_are_rejected():
    with pytest.raises(FeedError, match="does not match"):
        feed_with(lambda r: httpx.Response(200, json=payload(count=7))).fetch("reach_out", 200)
    with pytest.raises(FeedError, match="asked for"):
        feed_with(lambda r: httpx.Response(200, json=payload(view="engaged"))).fetch("reach_out", 200)


def test_limit_is_always_explicit_and_in_range():
    with pytest.raises(FeedError):
        feed_with(lambda r: httpx.Response(200, json=payload())).fetch("reach_out", 201)
    with pytest.raises(FeedError):
        feed_with(lambda r: httpx.Response(200, json=payload())).fetch("reach_out", 0)


def test_build_feed_precedence_and_not_configured():
    s = SimpleNamespace(explorer_queue_feed_path="x.json", explorer_queue_feed_url=URL, cora_service_token=TOKEN)
    assert isinstance(build_feed(s), FixtureFeed)  # fixture wins: nightly stays on the test feed
    s.explorer_queue_feed_path = ""
    assert isinstance(build_feed(s), LiveFeed)
    s.cora_service_token = ""
    with pytest.raises(FeedNotConfigured):
        build_feed(s)
    with pytest.raises(FeedNotConfigured):
        LiveFeed("", TOKEN)


# ── skipped-lead report ─────────────────────────────────────────────────────
@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[
        ExplorerQueueRun.__table__, ExplorerQueueContact.__table__, ExplorerQueueSkip.__table__,
        StaffCallQuality.__table__])
    with sessionmaker(bind=engine)() as s:
        yield s


def _settings(cap=200):
    return SimpleNamespace(
        ghl_location_id=LOC, explorer_queue_view="reach_out", explorer_queue_batch_cap=cap,
        explorer_queue_score_field_id="S", explorer_queue_ai_summary_field_id="W",
        explorer_queue_ai_campaign_field_id="A",
        explorer_queue_stage_map='{"NOT_STARTED": "ent-stage-not-started"}', explorer_queue_feed_path="")


def _run(session, rows, cap=200, mode="dry"):
    p = payload(0)
    p["rows"] = rows
    p["count"] = len(rows)
    feed = feed_with(lambda r: httpx.Response(200, json=p))
    flags = SimpleNamespace(explorer_queue_tagging_enabled=True, ghl_writes_enabled=True)
    with patch("app.core.mode_flags.get_mode_flags", return_value=flags):
        run = run_explorer_queue_cycle(
            session, _settings(cap), mode=mode, feed=feed, client=MagicMock(),
            contact_lookup=lambda cid: {"contact": {"tags": [], "customFields": []}},
            now=datetime(2026, 11, 6, 2, 0, tzinfo=timezone.utc))
    session.commit()
    return run


def _row(i, url):
    return {"enrollment_id": f"e{i}", "lead_id": f"l{i}", "primary_state": "NOT_STARTED", "score": 60 - i,
            "reasons": [], "ghl_url": url}


def test_skipped_rows_are_recorded_split_by_cause_even_in_a_dry_run(session):
    rows = [_row(1, None), _row(2, GOOD + "2"), _row(3, OTHER), _row(4, "not a url")]
    run = _run(session, rows)
    assert run.dry_run is True
    skips = {s.enrollment_id: s for s in session.execute(select(ExplorerQueueSkip)).scalars()}
    assert set(skips) == {"e1", "e3", "e4"}
    assert skips["e1"].reason == "no_ghl_contact" and skips["e1"].rank == 1 and skips["e1"].lead_id == "l1"
    assert skips["e4"].reason == "no_ghl_contact"
    assert skips["e3"].reason == "wrong_subaccount" and skips["e3"].ghl_location_id == "OTHERLOC"
    assert session.execute(select(ExplorerQueueContact)).first() is None  # dry run tags nothing


def test_dashboard_lists_latest_runs_skips_by_cause(session):
    _run(session, [_row(1, None), _row(2, OTHER)])
    out = get_explorer_queue_summary(session)
    assert [r["enrollment_id"] for r in out["skipped"]["no_ghl_contact"]] == ["e1"]
    assert [r["enrollment_id"] for r in out["skipped"]["wrong_subaccount"]] == ["e2"]


def test_feed_at_the_limit_is_flagged_capped_and_below_it_is_not(session):
    assert _run(session, [_row(i, GOOD + str(i)) for i in range(1, 4)], cap=3).feed_capped is True
    assert _run(session, [_row(i, GOOD + str(i)) for i in range(1, 4)], cap=4).feed_capped is False


def test_token_fingerprint_is_not_hardcoded_in_source():
    root = pathlib.Path(__file__).resolve().parents[2]
    assert "6b6b3af000c0" not in (root / "app" / "services" / "explorer_queue_feed.py").read_text(encoding="utf-8")
