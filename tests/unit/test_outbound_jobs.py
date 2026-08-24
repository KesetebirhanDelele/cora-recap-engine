"""
Unit tests for outbound_jobs.

_compute_window_run_at (bucket-occupancy search, not count-based):
  1. No pending jobs → returns window_start exactly (its own bucket is free)
  2. A job occupying window_start's bucket → next call advances to the next 75s bucket
  3. All 4 buckets in a 5-minute window occupied → advances to the next window (+300s)
  4. Jobs outside the 4-hour search range are not counted
  5. Completed/failed/cancelled jobs don't occupy a bucket
  6. Claimed jobs do occupy a bucket
  7. An arbitrary, off-grid timestamp (simulating a lead-requested exact-time
     callback) still reserves whichever bucket it falls into — collision
     avoidance, not just spacing approximation
  8. Search range fully occupied → falls back to the bucket past the range
     instead of looping forever

launch_outbound_call_job blocked-number guard:
  9. Phone on BLOCKED_DIAL_NUMBERS → job cancelled + exception created, no Synthflow call
  10. Phone not on blocklist → guard passes, normal flow continues
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
from app.worker.jobs.outbound_jobs import (
    _CALL_BATCH_SIZE,
    _CALL_WITHIN_SLOT_SPACING,
    _MAX_BUCKET_SEARCH,
    _bucket_index,
    _bucket_start,
    _bump_lower_priority_job,
    _compute_window_run_at,
)

# Bucket-aligned by construction (an exact multiple of 75s past _EPOCH), so
# tests don't depend on incidental alignment of an arbitrary wall-clock date.
_WINDOW_START = _bucket_start(1_000_000)


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


def _aware(dt: datetime) -> datetime:
    """
    Normalize a datetime read back from SQLite (naive) to UTC-aware, so it
    can be compared against values computed in-process (aware). Same
    normalization outbound_jobs.py::_bucket_index applies internally.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _pending_job(run_at: datetime, status: str = "pending", campaign_name: str | None = None) -> ScheduledJob:
    return ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=str(uuid.uuid4()),
        run_at=run_at,
        status=status,
        payload_json={"campaign_name": campaign_name} if campaign_name else None,
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )


# ─────────────────────────────────────────────────────────────────────────────


def test_no_pending_jobs_returns_window_start(session):
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_occupied_bucket_advances_to_next_bucket(session):
    session.add(_pending_job(_WINDOW_START))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)


def test_all_four_buckets_in_window_full_advances_to_next_window(session):
    for i in range(_CALL_BATCH_SIZE):
        session.add(_pending_job(_WINDOW_START + timedelta(seconds=i * _CALL_WITHIN_SLOT_SPACING)))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_BATCH_SIZE * _CALL_WITHIN_SLOT_SPACING)


def test_jobs_outside_four_hour_search_range_not_counted(session):
    outside = _WINDOW_START + timedelta(hours=4, seconds=1)
    session.add(_pending_job(outside))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_non_pending_statuses_dont_occupy_bucket(session):
    for status in ("completed", "failed", "cancelled"):
        session.add(_pending_job(_WINDOW_START, status=status))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START


def test_claimed_job_occupies_bucket(session):
    session.add(_pending_job(_WINDOW_START, status="claimed"))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)


def test_off_grid_timestamp_reserves_its_bucket(session):
    """
    Simulates a lead-requested exact-time callback: its run_at is an
    arbitrary offset, not aligned to a 0/75/150/225s boundary. It must still
    be detected as occupying its bucket, so a call computed afterward routes
    around it instead of colliding.
    """
    off_grid = _WINDOW_START + timedelta(seconds=40)  # same bucket as window_start (< 75s)
    session.add(_pending_job(off_grid))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)


def test_fully_occupied_search_range_falls_back_past_it(session):
    for i in range(_MAX_BUCKET_SEARCH):
        session.add(_pending_job(_WINDOW_START + timedelta(seconds=i * _CALL_WITHIN_SLOT_SPACING)))
    session.flush()
    result = _compute_window_run_at(session, _WINDOW_START)
    assert result == _WINDOW_START + timedelta(seconds=_MAX_BUCKET_SEARCH * _CALL_WITHIN_SLOT_SPACING)


# ── Priority / bump logic (spec/21) ─────────────────────────────────────────
#
# AC4: free bucket, priority job -> no bump, same as non-priority path.
# AC5: pending lower-priority occupant -> bumped to next free bucket.
# AC6: claimed/running occupant -> never touched, regardless of campaign.
# AC7: bumped job survives with only run_at/version changed.
# Plus: same-priority occupant and non-priority caller never bump anything.

def test_priority_job_free_bucket_takes_it_directly(session):
    result = _compute_window_run_at(session, _WINDOW_START, campaign_name="New Lead")
    assert result == _WINDOW_START


def test_priority_job_bumps_pending_lower_priority_occupant(session):
    cold_job = _pending_job(_WINDOW_START, campaign_name="Cold Lead")
    session.add(cold_job)
    session.flush()

    result = _compute_window_run_at(session, _WINDOW_START, campaign_name="New Lead")

    # New Lead takes the originally-contested bucket.
    assert result == _WINDOW_START
    # The bumped Cold Lead job moved to the next free bucket, version incremented.
    session.refresh(cold_job)
    assert _aware(cold_job.run_at) == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)
    assert cold_job.version == 1


def test_priority_job_never_bumps_claimed_occupant(session):
    claimed_job = _pending_job(_WINDOW_START, status="claimed", campaign_name="Cold Lead")
    session.add(claimed_job)
    session.flush()

    result = _compute_window_run_at(session, _WINDOW_START, campaign_name="New Lead")

    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)
    session.refresh(claimed_job)
    assert _aware(claimed_job.run_at) == _WINDOW_START  # untouched
    assert claimed_job.version == 0  # untouched


def test_priority_job_does_not_bump_another_priority_job(session):
    other_new_lead = _pending_job(_WINDOW_START, campaign_name="New Lead")
    session.add(other_new_lead)
    session.flush()

    result = _compute_window_run_at(session, _WINDOW_START, campaign_name="New Lead")

    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)
    session.refresh(other_new_lead)
    assert _aware(other_new_lead.run_at) == _WINDOW_START  # untouched


def test_non_priority_job_never_bumps_anything(session):
    """A Cold Lead job scheduling itself must not bump another pending Cold Lead job."""
    cold_job = _pending_job(_WINDOW_START, campaign_name="Cold Lead")
    session.add(cold_job)
    session.flush()

    result = _compute_window_run_at(session, _WINDOW_START, campaign_name="Cold Lead")

    assert result == _WINDOW_START + timedelta(seconds=_CALL_WITHIN_SLOT_SPACING)
    session.refresh(cold_job)
    assert _aware(cold_job.run_at) == _WINDOW_START  # untouched — incoming job isn't priority


def test_bumped_job_survives_with_only_run_at_and_version_changed(session):
    cold_job = _pending_job(_WINDOW_START, campaign_name="Cold Lead")
    original_entity_id = cold_job.entity_id
    session.add(cold_job)
    session.flush()

    _compute_window_run_at(session, _WINDOW_START, campaign_name="New Lead")

    session.refresh(cold_job)
    assert cold_job.job_type == "launch_outbound_call"
    assert cold_job.entity_id == original_entity_id
    assert cold_job.status == "pending"
    assert cold_job.payload_json["campaign_name"] == "Cold Lead"


def test_bump_lower_priority_job_skips_on_version_conflict(session):
    """
    Simulates a race: the occupant was claimed by a worker between being read
    (stale_job, holding the old version) and the bump attempt — the
    version-checked UPDATE must no-op rather than clobber the claim.
    """
    cold_job = _pending_job(_WINDOW_START, campaign_name="Cold Lead")
    session.add(cold_job)
    session.flush()
    stale_version = cold_job.version

    # Concurrent claim: version bumped, status advanced past pending.
    cold_job.version += 1
    cold_job.status = "claimed"
    session.flush()

    stale_job = SimpleNamespace(
        id=cold_job.id, version=stale_version, payload_json={"campaign_name": "Cold Lead"},
    )
    start_bucket = _bucket_index(_WINDOW_START) + 1

    _bump_lower_priority_job(
        session, stale_job,
        start_bucket=start_bucket, end_bucket=start_bucket + _MAX_BUCKET_SEARCH,
        occupied={},
    )

    session.refresh(cold_job)
    assert _aware(cold_job.run_at) == _WINDOW_START  # bump attempt was a safe no-op


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
