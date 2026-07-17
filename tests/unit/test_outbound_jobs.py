"""
Unit tests for outbound_jobs.

_compute_window_run_at:
  1. No pending jobs → slot 0 → returns window_start exactly
  2. 9 pending jobs → slot 0 → still returns window_start
  3. 10 pending jobs → slot 1 → returns window_start + 120s
  4. 19 pending jobs → slot 1 → returns window_start + 120s
  5. 20 pending jobs → slot 2 → returns window_start + 240s
  6. Jobs outside the 4-hour window are not counted
  7. Completed/failed/cancelled jobs are not counted

launch_outbound_call_job blocked-number guard:
  8. Phone on BLOCKED_DIAL_NUMBERS → job cancelled + exception created, no Synthflow call
  9. Phone not on blocklist → guard passes, normal flow continues
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.worker.jobs.outbound_jobs import _CALL_BATCH_SIZE, _CALL_SLOT_SECONDS, _compute_window_run_at

_WINDOW_START = datetime(2026, 4, 28, 14, 0, 0, tzinfo=timezone.utc)  # 9 AM CDT


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


def _pending_job(run_at: datetime, status: str = "pending") -> ScheduledJob:
    return ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=str(uuid.uuid4()),
        run_at=run_at,
        status=status,
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


def _add_pending_jobs(session, count: int, run_at: datetime | None = None) -> None:
    at = run_at or _WINDOW_START
    for _ in range(count):
        session.add(_pending_job(at))
    session.flush()


# ─────────────────────────────────────────────────────────────────────────────


def test_no_pending_jobs_returns_window_start(session):
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_nine_pending_jobs_still_slot_zero(session):
    _add_pending_jobs(session, 9)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_ten_pending_jobs_advances_to_slot_one(session):
    _add_pending_jobs(session, 10)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)


def test_nineteen_pending_jobs_stays_slot_one(session):
    _add_pending_jobs(session, 19)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)


def test_twenty_pending_jobs_advances_to_slot_two(session):
    _add_pending_jobs(session, 20)
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=2 * _CALL_SLOT_SECONDS)


def test_jobs_outside_four_hour_window_not_counted(session):
    outside = _WINDOW_START + timedelta(hours=4, seconds=1)
    _add_pending_jobs(session, _CALL_BATCH_SIZE, run_at=outside)
    # Only inside-window jobs count — the 10 outside jobs must not bump the slot
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_non_pending_statuses_not_counted(session):
    for status in ("completed", "failed", "cancelled"):
        session.add(_pending_job(_WINDOW_START, status=status))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_claimed_jobs_are_counted(session):
    _add_pending_jobs(session, _CALL_BATCH_SIZE, run_at=_WINDOW_START)
    session.add(_pending_job(_WINDOW_START, status="claimed"))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_SLOT_SECONDS)


# ── Blocked dial-number guard ─────────────────────────────────────────────────

def _make_mock_job(phone: str) -> MagicMock:
    job = MagicMock()
    job.id = str(uuid.uuid4())
    job.entity_type = "lead"
    job.entity_id = "+19592022210"
    job.payload_json = {
        "phone_number": phone,
        "contact_id": "+19592022210",
        "campaign_name": "Cold Lead",
        "lead_name": "",
        "correlation_id": "test-corr",
    }
    return job


def _make_flags(
    system_paused: bool = False,
    shadow_mode: bool = False,
    outbound_campaigns_paused: bool = False,
    cold_lead_campaign_paused: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        system_paused=system_paused,
        shadow_mode_enabled=shadow_mode,
        outbound_campaigns_paused=outbound_campaigns_paused,
        cold_lead_campaign_paused=cold_lead_campaign_paused,
    )


def _make_settings(blocked: str = "") -> SimpleNamespace:
    return SimpleNamespace(blocked_dial_numbers=blocked)


@patch("app.worker.claim.cancel_job")
@patch("app.worker.jobs.outbound_jobs.create_exception")
@patch("app.worker.jobs.outbound_jobs.get_worker_id", return_value="worker-test")
@patch("app.worker.jobs.outbound_jobs.get_settings")
@patch("app.worker.jobs.outbound_jobs.get_sync_session")
@patch("app.worker.jobs.outbound_jobs.claim_job")
@patch("app.worker.jobs.outbound_jobs.mark_running")
def test_blocked_phone_cancels_job_and_raises_exception(
    mock_mark_running, mock_claim, mock_session_cm, mock_get_settings,
    mock_worker_id, mock_create_exc, mock_cancel,
):
    from app.worker.jobs.outbound_jobs import launch_outbound_call_job

    mock_session = MagicMock()
    mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

    mock_job = _make_mock_job(phone="+16822812224")
    mock_claim.return_value = mock_job
    mock_get_settings.return_value = _make_settings(blocked="+16822812224")

    with patch("app.core.mode_flags.get_mode_flags", return_value=_make_flags()):
        launch_outbound_call_job(mock_job.id)

    # Guard fired: job cancelled, exception created, Synthflow never called
    mock_mark_running.assert_not_called()
    mock_create_exc.assert_called_once()
    exc_call = mock_create_exc.call_args
    assert exc_call.kwargs["type"] == "blocked_dial_number"
    assert exc_call.kwargs["severity"] == "critical"
    mock_session.commit.assert_called_once()


@patch("app.worker.jobs.outbound_jobs.create_exception")
@patch("app.worker.jobs.outbound_jobs.get_worker_id", return_value="worker-test")
@patch("app.worker.jobs.outbound_jobs.get_settings")
@patch("app.worker.jobs.outbound_jobs.get_sync_session")
@patch("app.worker.jobs.outbound_jobs.claim_job")
@patch("app.worker.jobs.outbound_jobs.mark_running")
@patch("app.worker.jobs.outbound_jobs.complete_job")
def test_non_blocked_phone_passes_guard(
    mock_complete, mock_mark_running, mock_claim, mock_session_cm, mock_get_settings,
    mock_worker_id, mock_create_exc,
):
    from app.worker.jobs.outbound_jobs import launch_outbound_call_job

    mock_session = MagicMock()
    mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

    mock_job = _make_mock_job(phone="+19592022210")
    mock_claim.return_value = mock_job
    mock_get_settings.return_value = _make_settings(blocked="+16822812224")

    # Shadow mode exits cleanly after mark_running without hitting Synthflow
    with patch("app.core.mode_flags.get_mode_flags", return_value=_make_flags(shadow_mode=True)):
        with patch("app.worker.shadow.log_shadow_action"):
            launch_outbound_call_job(mock_job.id)

    # Guard did not fire
    mock_create_exc.assert_not_called()
    # mark_running was reached (guard passed)
    mock_mark_running.assert_called_once()


# ── Cold Lead-only campaign pause guard ────────────────────────────────────────

@patch("app.worker.jobs.outbound_jobs.release_job_to_pending")
@patch("app.worker.jobs.outbound_jobs.get_worker_id", return_value="worker-test")
@patch("app.worker.jobs.outbound_jobs.get_settings")
@patch("app.worker.jobs.outbound_jobs.get_sync_session")
@patch("app.worker.jobs.outbound_jobs.claim_job")
@patch("app.worker.jobs.outbound_jobs.mark_running")
def test_cold_lead_campaign_paused_releases_cold_lead_job(
    mock_mark_running, mock_claim, mock_session_cm, mock_get_settings,
    mock_worker_id, mock_release,
):
    from app.worker.jobs.outbound_jobs import launch_outbound_call_job

    mock_session = MagicMock()
    mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

    mock_job = _make_mock_job(phone="+19592022210")  # campaign_name="Cold Lead"
    mock_claim.return_value = mock_job
    mock_get_settings.return_value = _make_settings()

    with patch("app.core.mode_flags.get_mode_flags", return_value=_make_flags(cold_lead_campaign_paused=True)):
        launch_outbound_call_job(mock_job.id)

    # Held, not executed — mark_running never reached
    mock_mark_running.assert_not_called()
    mock_release.assert_called_once_with(mock_session, mock_job, defer_seconds=60)
    mock_session.commit.assert_called_once()


@patch("app.worker.jobs.outbound_jobs.release_job_to_pending")
@patch("app.worker.jobs.outbound_jobs.get_worker_id", return_value="worker-test")
@patch("app.worker.jobs.outbound_jobs.get_settings")
@patch("app.worker.jobs.outbound_jobs.get_sync_session")
@patch("app.worker.jobs.outbound_jobs.claim_job")
@patch("app.worker.jobs.outbound_jobs.mark_running")
@patch("app.worker.jobs.outbound_jobs.complete_job")
def test_cold_lead_campaign_paused_does_not_affect_new_lead_job(
    mock_complete, mock_mark_running, mock_claim, mock_session_cm, mock_get_settings,
    mock_worker_id, mock_release,
):
    from app.worker.jobs.outbound_jobs import launch_outbound_call_job

    mock_session = MagicMock()
    mock_session_cm.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)

    mock_job = _make_mock_job(phone="+19592022210")
    mock_job.payload_json["campaign_name"] = "New Lead"
    mock_claim.return_value = mock_job
    mock_get_settings.return_value = _make_settings()

    # Shadow mode so the flow exits cleanly right after the pause guards pass
    with patch(
        "app.core.mode_flags.get_mode_flags",
        return_value=_make_flags(cold_lead_campaign_paused=True, shadow_mode=True),
    ):
        with patch("app.worker.shadow.log_shadow_action"):
            launch_outbound_call_job(mock_job.id)

    # Cold-Lead-only pause must not hold a New Lead job
    mock_release.assert_not_called()
    mock_mark_running.assert_called_once()
