"""The isolated staff-quality-scan queue: not queue lag / stuck jobs (spec/29); its own staleness alert; one chain only.
Opt-in DB-backed: WRONG_DATE_TEST_DATABASE_URL (throwaway DB only)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DB_URL = os.environ.get("WRONG_DATE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set WRONG_DATE_TEST_DATABASE_URL to run")


@pytest.fixture()
def session():
    engine = create_engine(DB_URL)
    with Session(engine) as s:
        s.execute(text("DELETE FROM scheduled_jobs"))
        s.execute(text("DELETE FROM alert_events WHERE alert_type = 'staff_quality_scan_stale'"))
        s.commit()
        yield s
        s.rollback()
    engine.dispose()


def _job(session, job_type, status, run_ago_s=0, lease_in_s=None, updated_ago_s=0):
    now = datetime.now(tz=timezone.utc)
    session.execute(text("""INSERT INTO scheduled_jobs (id, job_type, entity_type, entity_id, run_at, status, version, lease_expires_at, created_at, updated_at)
                            VALUES (:i,:t,'system','x',:r,:s,0,:l,:u,:u)"""),
                    {"i": str(uuid.uuid4()), "t": job_type, "r": now - timedelta(seconds=run_ago_s), "s": status,
                     "l": (now + timedelta(seconds=lease_in_s)) if lease_in_s is not None else None,
                     "u": now - timedelta(seconds=updated_ago_s)})
    session.commit()


def test_waiting_quality_scans_are_not_queue_lag_or_stuck_jobs(session):
    from app.services.dashboard_metrics import queue_lag_seconds, stuck_pending_count

    _job(session, "staff_call_quality_scan", "pending", run_ago_s=1000)
    _job(session, "staff_call_quality_scan", "pending", run_ago_s=400)
    assert queue_lag_seconds(session) == 0.0 and stuck_pending_count(session) == 0


def test_a_late_ordinary_job_still_counts(session):
    from app.services.dashboard_metrics import queue_lag_seconds, stuck_pending_count

    _job(session, "staff_call_quality_scan", "pending", run_ago_s=1000)
    _job(session, "launch_outbound_call", "pending", run_ago_s=700)
    assert 650 < queue_lag_seconds(session) < 760 and stuck_pending_count(session) == 1


def _stale(session, settings):
    from app.services import alerting

    now = datetime.now(tz=timezone.utc)
    with patch.object(alerting, "_smtp_send"):
        alerting._evaluate_quality_scan_stale(session, settings, now, timedelta(seconds=60))
        session.commit()
    return session.execute(text("SELECT status FROM alert_events WHERE alert_type='staff_quality_scan_stale' ORDER BY created_at DESC LIMIT 1")).scalar()


def _settings(**kw):
    base = dict(staff_call_quality_scan_enabled=True, alert_quality_scan_stale_minutes=60, smtp_enabled=False,
                alert_email_to="x@example.test", alert_email_from="y@example.test", alert_dedup_window_seconds=60)
    base.update(kw)
    return NS(**base)


def test_stale_scan_alert_fires_only_when_no_scan_completed_recently_and_none_is_running(session):
    st = _settings()
    _job(session, "staff_call_quality_scan", "completed", updated_ago_s=90 * 60)
    assert _stale(session, st) == "active"                                     # last completion 90 min ago
    _job(session, "staff_call_quality_scan", "completed", updated_ago_s=5 * 60)
    assert _stale(session, st) == "resolved"                                   # a fresh completion clears it


def test_a_scan_running_with_a_live_lease_is_given_its_time_and_disabled_scan_never_alerts(session):
    st = _settings()
    _job(session, "staff_call_quality_scan", "completed", updated_ago_s=90 * 60)
    _job(session, "staff_call_quality_scan", "running", lease_in_s=600)
    assert _stale(session, st) is None                                         # running right now: no alert
    session.execute(text("DELETE FROM scheduled_jobs"))
    session.commit()
    assert _stale(session, _settings(staff_call_quality_scan_enabled=False)) is None


def test_only_one_scan_chain_exists_after_restart_and_reschedule(session):
    from app.worker.jobs import staff_call_quality_jobs as q

    class _S:                                         # get_sync_session() stand-in using the test session
        def __enter__(self):
            return session

        def __exit__(self, *a):
            session.commit()

    with patch("app.db.get_sync_session", lambda: _S()):
        _job(session, "staff_call_quality_scan", "running", lease_in_s=600)
        q.start_staff_call_quality_scanner()                                   # a restart while a scan is running
        n = session.execute(text("SELECT count(*) FROM scheduled_jobs WHERE job_type='staff_call_quality_scan' AND status IN ('pending','claimed','running')")).scalar()
        assert n == 1                                                          # no second chain
        q._reschedule(session)                                                 # the running scan finishes -> next one
        q._reschedule(session)                                                 # a duplicate finisher must not add another
        session.commit()
        pend = session.execute(text("SELECT count(*) FROM scheduled_jobs WHERE job_type='staff_call_quality_scan' AND status='pending'")).scalar()
        assert pend == 1
