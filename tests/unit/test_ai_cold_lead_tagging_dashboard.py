"""
Tests for app/services/dashboard_metrics.py::get_ai_cold_lead_tagging_runs
(spec/31).

Uses an isolated in-memory SQLite table (just tag_ai_cold_leads_runs, not
the full Base.metadata) — no GHL calls involved, unlike get_card_metrics()
which also computes the live-GHL backlog count and is intentionally not
exercised here to avoid any risk of a real network call in a unit test.

Covers:
  1. Empty table returns {"runs": []}, not an error
  2. Rows ordered started_at DESC
  3. limit is respected
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tag_ai_cold_leads_run import TagAiColdLeadsRun
from app.services.dashboard_metrics import get_ai_cold_lead_tagging_runs


@pytest.fixture
def engine():
    from sqlalchemy.pool import StaticPool
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TagAiColdLeadsRun.__table__.create(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess


def _make_run(session: Session, started_at: datetime, **overrides) -> TagAiColdLeadsRun:
    run = TagAiColdLeadsRun(
        id=str(uuid.uuid4()),
        started_at=started_at,
        finished_at=started_at + timedelta(minutes=5),
        status=overrides.pop("status", "completed"),
        dry_run=overrides.pop("dry_run", False),
        contacts_scanned=overrides.pop("contacts_scanned", 10),
        contacts_tagged=overrides.pop("contacts_tagged", 5),
        contacts_skipped_already_tagged=overrides.pop("contacts_skipped_already_tagged", 0),
        contacts_failed=overrides.pop("contacts_failed", 0),
    )
    session.add(run)
    session.commit()
    return run


def test_empty_table_returns_empty_list(session):
    result = get_ai_cold_lead_tagging_runs(session)
    assert result == {"runs": []}


def test_runs_ordered_newest_first(session):
    now = datetime.now(tz=timezone.utc)
    _make_run(session, now - timedelta(days=2))
    newest = _make_run(session, now)
    _make_run(session, now - timedelta(days=1))

    result = get_ai_cold_lead_tagging_runs(session)

    assert result["runs"][0]["id"] == newest.id
    started_ats = [r["started_at"] for r in result["runs"]]
    assert started_ats == sorted(started_ats, reverse=True)


def test_limit_is_respected(session):
    now = datetime.now(tz=timezone.utc)
    for i in range(5):
        _make_run(session, now - timedelta(hours=i))

    result = get_ai_cold_lead_tagging_runs(session, limit=2)

    assert len(result["runs"]) == 2
