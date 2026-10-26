"""Sign-up intake trend (spec/40): classification, Central-day counting, the hourly rebuild, snapshots and the page's data."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services import signup_intake as si

UTC = timezone.utc
NOW = datetime(2026, 10, 26, 15, 30, tzinfo=UTC)               # Monday 10:30 AM Central


def contact(created_utc, *, tags=("src-free_signup",), source="api v1", email="a@x.com", phone=""):
    return {"id": created_utc + email, "dateAdded": created_utc, "tags": list(tags), "source": source, "email": email, "phone": phone}


class FakeGHL:
    """Pages of contacts, 100 per call, like GHL's /contacts/search; records each filter it was asked for."""

    def __init__(self, contacts, fail=False):
        self.contacts, self.fail, self.calls = contacts, fail, []

    def search_contacts(self, filters, *, page_limit=100, search_after=None):
        self.calls.append((filters[0]["value"], search_after))
        if self.fail:
            raise RuntimeError("GHL down")
        lo, hi = filters[0]["value"]["gte"], filters[0]["value"]["lte"]
        rows = [c for c in self.contacts if lo <= int(datetime.fromisoformat(c["dateAdded"].replace("Z", "+00:00")).timestamp() * 1000) <= hi]
        start = int(search_after[0]) if search_after else 0
        page = rows[start:start + page_limit]
        for i, c in enumerate(page):
            c["searchAfter"] = [start + i + 1, c["id"]]
        return {"contacts": page}


@pytest.fixture()
def session():
    eng = create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE signup_daily (day DATE, segment TEXT, signups INT DEFAULT 0, with_email INT DEFAULT 0, with_phone INT DEFAULT 0, "
                       "with_both INT DEFAULT 0, with_campaign INT DEFAULT 0, utm_fb INT DEFAULT 0, utm_ig INT DEFAULT 0, updated_at TIMESTAMP, PRIMARY KEY (day, segment))"))
        c.execute(text("CREATE TABLE signup_snapshot (taken_at TIMESTAMP, segment TEXT, total INT, with_email INT, with_phone INT, PRIMARY KEY (taken_at, segment))"))
    with Session(eng) as s:
        yield s


def daily(session):
    return {(str(r[0]), r[1]): tuple(r[2:]) for r in session.execute(text(
        "SELECT day, segment, signups, with_email, with_phone, with_both FROM signup_daily ORDER BY day, segment")).fetchall()}


# -- classification -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("tags,source,want", [
    (["src-free_signup", "brand-training_colaberry"], "api v1", "explorer"),
    ([], "api v1", "explorer"),                                           # early contacts, created before tagging
    (["src-free_signup"], "", "explorer"),
    (["src-open_house", "brand-open_house"], "api v1", "open_house"),
    (["brand-open_house"], "", "open_house"),
    (["SRC-OPEN_HOUSE"], "api v1", "open_house"),                         # case-insensitive
    (["ai explorer", "left voicemail"], "", None),                        # a normal Cora lead
    ([], "School Catalog Request Form", None),
    ([], "", None),
])
def test_classify(tags, source, want):
    assert si.classify({"tags": tags, "source": source}) == want


# -- counting by Central day ----------------------------------------------------------------------------------------
def test_central_day_boundary_is_midnight_central_not_utc():
    rows = si.summarize([
        contact("2026-10-24T04:30:00Z"),                                   # 11:30 PM Friday Central
        contact("2026-10-24T05:10:00Z", email="b@x.com"),                  # 12:10 AM Saturday Central (CDT = UTC-5)
    ])
    assert {k[0] for k in rows} == {date(2026, 10, 23), date(2026, 10, 24)}


def test_counts_email_phone_both_campaign_and_channel_tags():
    rows = si.summarize([
        contact("2026-10-25T18:00:00Z", email="a@x.com", phone="+1555", tags=("src-free_signup", "camp-1", "utm-fb")),
        contact("2026-10-25T18:01:00Z", email="b@x.com", tags=("src-free_signup", "utm-ig")),
        contact("2026-10-25T18:02:00Z", email="", phone="+1556"),
        contact("2026-10-25T18:03:00Z", email="  ", phone=""),
    ])
    v = rows[(date(2026, 10, 25), "explorer")]
    assert v == {"signups": 4, "with_email": 2, "with_phone": 2, "with_both": 1, "with_campaign": 1, "utm_fb": 1, "utm_ig": 1}


def test_non_signups_and_bad_dates_are_skipped():
    rows = si.summarize([contact("2026-10-25T18:00:00Z", tags=("ai explorer",), source=""), contact("not-a-date"),
                         {"tags": ["src-free_signup"]}])
    assert rows == {}


# -- the hourly rebuild -----------------------------------------------------------------------------------------------
def test_first_run_reads_from_the_history_start_and_stores_both_segments(session):
    ghl = FakeGHL([contact("2026-10-23T22:00:00Z"), contact("2026-10-25T20:00:00Z", tags=("src-open_house",), phone="+1555", email="o@x.com")])
    out = si.run_sync(session, NS(), NOW, ghl)
    assert out["first_run"] and out["signups"] == 2
    assert ghl.calls[0][0]["gte"] == int(si.HISTORY_START.timestamp() * 1000)
    assert daily(session) == {("2026-10-23", "explorer"): (1, 1, 0, 0), ("2026-10-25", "open_house"): (1, 1, 1, 1)}
    snap = {r[0]: tuple(r[1:]) for r in session.execute(text("SELECT segment, total, with_email, with_phone FROM signup_snapshot")).fetchall()}
    assert snap == {"explorer": (1, 1, 0), "open_house": (1, 1, 1)}


def test_later_runs_rebuild_only_the_last_seven_days_and_keep_older_history(session):
    session.execute(text("INSERT INTO signup_daily (day, segment, signups, with_email, with_phone, with_both) VALUES ('2026-10-12', 'explorer', 9, 9, 0, 0)"))
    session.execute(text("INSERT INTO signup_daily (day, segment, signups, with_email, with_phone, with_both) VALUES ('2026-10-25', 'explorer', 99, 99, 0, 0)"))
    ghl = FakeGHL([contact("2026-10-25T18:00:00Z", phone="+1555")])
    si.run_sync(session, NS(), NOW, ghl)
    assert ghl.calls[0][0]["gte"] == int(datetime(2026, 10, 20, 0, 0, tzinfo=si.TZ).timestamp() * 1000)     # today - 6 days, Central midnight
    d = daily(session)
    assert d[("2026-10-12", "explorer")] == (9, 9, 0, 0)                                                      # untouched
    assert d[("2026-10-25", "explorer")] == (1, 1, 1, 1)                                                      # replaced, not added to


def test_running_twice_never_double_counts_and_a_phone_added_later_is_picked_up(session):
    c = contact("2026-10-25T18:00:00Z")
    ghl = FakeGHL([c])
    si.run_sync(session, NS(), NOW, ghl)
    si.run_sync(session, NS(), NOW + timedelta(minutes=5), ghl)
    assert daily(session)[("2026-10-25", "explorer")] == (1, 1, 0, 0)
    c["phone"] = "+1555"                                                   # the lead adds a phone
    si.run_sync(session, NS(), NOW + timedelta(hours=1), ghl)
    assert daily(session)[("2026-10-25", "explorer")] == (1, 1, 1, 1)
    snaps = session.execute(text("SELECT COUNT(*) FROM signup_snapshot WHERE segment = 'explorer'")).scalar()
    assert snaps == 2                                                      # one row per hour: 10:00 (re-run replaced) and 11:00


def test_a_failed_ghl_read_leaves_the_tables_exactly_as_they_were(session):
    si.run_sync(session, NS(), NOW, FakeGHL([contact("2026-10-25T18:00:00Z")]))
    before = daily(session)
    with pytest.raises(RuntimeError):
        si.run_sync(session, NS(), NOW + timedelta(hours=1), FakeGHL([], fail=True))
    assert daily(session) == before


def test_paging_follows_search_after_and_reads_every_contact(session):
    many = [contact("2026-10-25T18:00:00Z", email=f"u{i}@x.com") for i in range(230)]
    ghl = FakeGHL(many)
    out = si.run_sync(session, NS(), NOW, ghl)
    assert out["contacts_read"] == 230 and len(ghl.calls) == 3 and daily(session)[("2026-10-25", "explorer")][0] == 230


def test_a_window_too_large_to_page_fails_loudly_instead_of_undercounting(session, monkeypatch):
    monkeypatch.setattr(si, "MAX_PAGES", 2)
    with pytest.raises(RuntimeError, match="partial count"):
        si.run_sync(session, NS(), NOW, FakeGHL([contact("2026-10-25T18:00:00Z", email=f"u{i}@x.com") for i in range(250)]))
    assert daily(session) == {}


# -- the page's data -------------------------------------------------------------------------------------------------
def test_trend_zero_fills_days_and_computes_percentages(session):
    ghl = FakeGHL([contact("2026-10-23T22:00:00Z"), contact("2026-10-23T22:05:00Z", phone="+1", email="b@x.com"),
                   contact("2026-10-25T20:00:00Z", tags=("src-open_house",), phone="+2", email="o@x.com")])
    si.run_sync(session, NS(), NOW, ghl)
    t = si.get_trend(session, 30, NOW)
    assert t["first_tracked_day"] == "2026-10-23" and [x["day"] for x in t["days"]] == ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26"]
    assert t["days"][1]["explorer"]["signups"] == 0 and t["days"][1]["open_house"]["signups"] == 0          # zero-filled
    ex = t["alltime"]["explorer"]
    assert (ex["signups"], ex["with_email"], ex["with_phone"], ex["email_pct"], ex["phone_pct"]) == (2, 2, 1, 100.0, 50.0)
    assert t["alltime"]["open_house"]["phone_pct"] == 100.0
    assert t["last_refreshed"] and t["timezone"] == "America/Chicago"


def test_trend_cumulative_uses_the_last_snapshot_of_each_central_day(session):
    c = contact("2026-10-25T12:00:00Z")                                     # Sunday 7 AM Central
    ghl = FakeGHL([c])
    si.run_sync(session, NS(), NOW - timedelta(days=1, hours=2), ghl)       # Sunday 8:30 AM Central: no phone yet
    c["phone"] = "+1555"
    si.run_sync(session, NS(), NOW - timedelta(days=1), ghl)                # Sunday 10:30 AM Central: phone arrived (same Central day, later snapshot)
    si.run_sync(session, NS(), NOW, ghl)
    cum = {x["day"]: x for x in si.get_trend(session, 30, NOW)["cumulative"]}
    assert cum["2026-10-25"]["explorer"]["with_phone"] == 1 and cum["2026-10-26"]["explorer"]["total"] == 1


def test_trend_on_an_empty_database_says_nothing_is_tracked_yet(session):
    t = si.get_trend(session, 30, NOW)
    assert t["first_tracked_day"] is None and t["last_refreshed"] is None and t["cumulative"] == []
    assert t["alltime"]["explorer"]["signups"] == 0 and t["alltime"]["explorer"]["email_pct"] == 0.0


def test_the_job_is_registered_with_the_worker_and_the_migration_follows_0029():
    from app.worker import main
    assert main._JOB_QUEUE_ATTRS["signup_intake_sync"] == "rq_default_queue" and main._JOB_TIMEOUT_SECONDS["signup_intake_sync"] == 600
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0030_signup_intake.py").read_text(encoding="utf-8")
    assert re.search(r'^revision = "0030"$', src, re.M) and re.search(r'^down_revision = "0029"$', src, re.M)
    assert "signup_daily" in src and "signup_snapshot" in src
