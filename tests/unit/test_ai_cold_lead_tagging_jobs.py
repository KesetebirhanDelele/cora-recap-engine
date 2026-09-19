"""
Tests for app/worker/jobs/ai_cold_lead_tagging_jobs.py (spec/31).

First test coverage for this repo's worker-job-wrapper lifecycle layer
(claim -> run -> complete/fail -> reschedule). Kept lifecycle-focused —
app/services/ai_cold_lead_tagging.py's own filter/tagging logic is tested
separately in tests/unit/test_ai_cold_lead_tagging.py.

All imports inside the job module are local (module-level, resolved at call
time), so patches target the SOURCE modules (app.worker.claim, app.db,
app.config, app.worker.exceptions, app.worker.scheduler,
app.services.ai_cold_lead_tagging) rather than the job module's namespace.

Covers:
  1. Happy path — claim succeeds, run_tagging_cycle called once, complete_job
     called, reschedule always runs in `finally`.
  2. Lost claim race — claim_job returns None, function returns early,
     run_tagging_cycle never called, no reschedule (never reached claim).
  3. Exception path — run_tagging_cycle raises: fail_job + create_exception
     called, complete_job NOT called, and _reschedule STILL runs in
     `finally` — regression guard for the "orphaned unscheduled job" bug
     class spec/29 documents for a too-short lease / crashed cycle.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.worker.jobs.ai_cold_lead_tagging_jobs import ai_cold_lead_tagging_scan_job


def _mock_job(job_id: str = "job-1") -> MagicMock:
    job = MagicMock()
    job.id = job_id
    return job


@patch("app.worker.scheduler.schedule_job")
@patch("app.services.ai_cold_lead_tagging.run_tagging_cycle")
@patch("app.worker.exceptions.create_exception")
@patch("app.worker.claim.fail_job")
@patch("app.worker.claim.complete_job")
@patch("app.worker.claim.mark_running")
@patch("app.worker.claim.claim_job")
@patch("app.worker.claim.get_worker_id", return_value="worker-test")
@patch("app.db.get_sync_session")
@patch("app.config.get_settings")
def test_happy_path_completes_and_reschedules(
    mock_get_settings, mock_get_session, mock_get_worker_id, mock_claim,
    mock_mark_running, mock_complete, mock_fail, mock_create_exc,
    mock_run_cycle, mock_schedule,
):
    session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = session
    job = _mock_job()
    mock_claim.return_value = job

    ai_cold_lead_tagging_scan_job("job-1")

    mock_claim.assert_called_once()
    mock_mark_running.assert_called_once_with(session, job)
    mock_run_cycle.assert_called_once_with(session, mock_get_settings.return_value)
    mock_complete.assert_called_once_with(session, job)
    mock_fail.assert_not_called()
    mock_schedule.assert_called_once()  # _reschedule always runs


@patch("app.worker.scheduler.schedule_job")
@patch("app.services.ai_cold_lead_tagging.run_tagging_cycle")
@patch("app.worker.claim.claim_job", return_value=None)
@patch("app.worker.claim.get_worker_id", return_value="worker-test")
@patch("app.db.get_sync_session")
@patch("app.config.get_settings")
def test_lost_claim_race_returns_early(
    mock_get_settings, mock_get_session, mock_get_worker_id, mock_claim,
    mock_run_cycle, mock_schedule,
):
    session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = session

    ai_cold_lead_tagging_scan_job("job-1")

    mock_run_cycle.assert_not_called()
    mock_schedule.assert_not_called()  # never reached claim -> no reschedule either


@patch("app.worker.scheduler.schedule_job")
@patch("app.services.ai_cold_lead_tagging.run_tagging_cycle", side_effect=RuntimeError("boom"))
@patch("app.worker.exceptions.create_exception")
@patch("app.worker.claim.fail_job")
@patch("app.worker.claim.complete_job")
@patch("app.worker.claim.mark_running")
@patch("app.worker.claim.claim_job")
@patch("app.worker.claim.get_worker_id", return_value="worker-test")
@patch("app.db.get_sync_session")
@patch("app.config.get_settings")
def test_exception_path_fails_job_and_still_reschedules(
    mock_get_settings, mock_get_session, mock_get_worker_id, mock_claim,
    mock_mark_running, mock_complete, mock_fail, mock_create_exc,
    mock_run_cycle, mock_schedule,
):
    session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = session
    job = _mock_job()
    mock_claim.return_value = job

    ai_cold_lead_tagging_scan_job("job-1")

    # Regression guard (2026-09-18 production incident): a failed flush
    # inside run_tagging_cycle leaves the real session's transaction
    # aborted — fail_job/_reschedule must not touch it again without a
    # rollback first, or they raise PendingRollbackError and the job is
    # left permanently stuck with no next run ever scheduled. This mock
    # session can't reproduce that cascade itself (MagicMock doesn't model
    # SQLAlchemy's transaction state machine), so assert the rollback call
    # directly as the contract that prevents it.
    session.rollback.assert_called_once()
    mock_fail.assert_called_once()
    assert mock_fail.call_args.kwargs["reason"] == "boom"
    mock_create_exc.assert_called_once()
    mock_complete.assert_not_called()
    # Regression guard (spec/29): a crashed cycle must not leave the job
    # unscheduled — _reschedule runs in `finally` regardless of outcome.
    mock_schedule.assert_called_once()
