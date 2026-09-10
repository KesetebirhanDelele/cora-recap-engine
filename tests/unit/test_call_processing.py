"""
Unit tests for call_processing.py — spec 14 implementation.

Covers:
  1.  normalize_synthflow_outcome: Synthflow call_status field used
  2.  normalize_synthflow_outcome: fallback to legacy 'status' field
  3.  normalize_synthflow_outcome: hangup_on_voicemail routes to voicemail
  4.  normalize_synthflow_outcome: end_call_reason=voicemail forces voicemail path
  5.  normalize_synthflow_outcome: completed routes to call-through
  6.  normalize_synthflow_outcome: unknown status passes through
  7.  _parse_datetime: ISO string with Z
  8.  _parse_datetime: epoch integer
  9.  _parse_datetime: None returns None
  10. _parse_datetime: ISO string with offset
  11. _log_executed_actions: failed action is logged as warning
  12. _log_executed_actions: successful action is not logged as warning
  13. _log_executed_actions: empty executed_actions is a no-op
  14. _create_call_event: creates row from Synthflow payload
  15. _create_call_event: dedupe replay returns existing row
  16. _create_call_event: maps all Synthflow-specific fields
  17. process_call_event: voicemail route (call_status field) creates CallEvent + routes
  18. process_call_event: call-through route (call_status=completed) creates CallEvent + routes
  19. process_call_event: missing call_id creates exception + fails job
  20. process_call_event: unknown status creates exception + fails job
  21. normalize_synthflow_outcome: left_voicemail is a voicemail status
  22. normalize_synthflow_outcome: voicemail_detected is a voicemail status
  23. normalize_synthflow_outcome: machine_detected is a voicemail status
  24. process_call_event: left_voicemail routes to voicemail path
  25. process_call_event: voicemail_detected routes to voicemail path
  26. process_call_event: machine_detected routes to voicemail path
  27. VOICEMAIL_STATUSES: all five variants present in the public constant
  28. _FAILED_STATUSES: user-canceled is treated as a terminal failure, not unknown
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.worker.jobs.call_processing import (
    VOICEMAIL_STATUSES,
    _create_call_event,
    _FAILED_STATUSES,
    _log_executed_actions,
    _parse_datetime,
    normalize_synthflow_outcome,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

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


def _make_job(session: Session, payload: dict) -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_call_event",
        entity_type="call",
        entity_id=payload.get("call_id", "test-call"),
        status="pending",
        run_at=datetime.now(tz=timezone.utc),
        payload_json=payload,
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()
    return job


# ─────────────────────────────────────────────────────────────────────────────
# 1–6: normalize_synthflow_outcome
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_uses_call_status_field():
    """Synthflow's call_status field is the primary routing key."""
    result = normalize_synthflow_outcome({"call_status": "completed", "status": "other"})
    assert result == "completed"


def test_normalize_falls_back_to_status():
    """If call_status is absent, falls back to status (legacy/test payloads)."""
    result = normalize_synthflow_outcome({"status": "completed"})
    assert result == "completed"


def test_normalize_hangup_on_voicemail():
    result = normalize_synthflow_outcome({
        "call_status": "hangup_on_voicemail",
        "end_call_reason": "voicemail",
    })
    assert result == "hangup_on_voicemail"


def test_normalize_end_call_reason_forces_voicemail():
    """end_call_reason=voicemail routes to voicemail even if call_status is missing."""
    result = normalize_synthflow_outcome({"end_call_reason": "voicemail"})
    assert result == "voicemail"


def test_normalize_completed():
    result = normalize_synthflow_outcome({"call_status": "completed"})
    assert result == "completed"


def test_normalize_unknown_passes_through():
    result = normalize_synthflow_outcome({"call_status": "some_unknown_status"})
    assert result == "some_unknown_status"


# ─────────────────────────────────────────────────────────────────────────────
# 7–10: _parse_datetime
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_datetime_iso_z():
    dt = _parse_datetime("2024-01-15T14:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2024


def test_parse_datetime_epoch_int():
    dt = _parse_datetime(1705329600)  # 2024-01-15 12:00:00 UTC
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_datetime_none():
    assert _parse_datetime(None) is None


def test_parse_datetime_iso_with_offset():
    dt = _parse_datetime("2024-01-15T09:00:00-05:00")
    assert dt is not None
    assert dt.tzinfo is not None


# ─────────────────────────────────────────────────────────────────────────────
# 11–13: _log_executed_actions
# ─────────────────────────────────────────────────────────────────────────────

def test_log_executed_actions_failure_logs_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.worker.jobs.call_processing"):
        _log_executed_actions("call-1", {
            "executed_actions": [{"name": "ghl_lookup", "status_code": 401}]
        })
    assert any("FAILED" in r.message for r in caplog.records)
    assert any("ghl_lookup" in r.message for r in caplog.records)


def test_log_executed_actions_success_not_warning(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="app.worker.jobs.call_processing"):
        _log_executed_actions("call-1", {
            "executed_actions": [{"name": "ghl_lookup", "status_code": 200}]
        })
    assert not any("FAILED" in r.message for r in caplog.records)


def test_log_executed_actions_empty_is_noop():
    # Should not raise
    _log_executed_actions("call-1", {})
    _log_executed_actions("call-1", {"executed_actions": []})


# ─────────────────────────────────────────────────────────────────────────────
# 14–16: _create_call_event
# ─────────────────────────────────────────────────────────────────────────────

def test_create_call_event_creates_row(session):
    call_id = str(uuid.uuid4())
    payload = {
        "call_id": call_id,
        "call_status": "completed",
        "transcript": "Hello world",
        "recording_url": "https://example.com/rec.mp3",
        "duration": 90,
        "model_id": "model-abc",
        "lead_name": "Jane Doe",
        "agent_phone_number": "+15550001111",
        "timeline": [{"event": "answer", "t": 0}],
        "telephony_duration": 91.5,
        "telephony_start": "2024-01-15T14:00:00Z",
        "telephony_end": "2024-01-15T14:01:31Z",
    }
    ce = _create_call_event(session, call_id, payload, "completed")

    assert ce.call_id == call_id
    assert ce.status == "completed"
    assert ce.transcript == "Hello world"
    assert ce.duration_seconds == 90
    assert ce.recording_url == "https://example.com/rec.mp3"
    assert ce.model_id == "model-abc"
    assert ce.lead_name == "Jane Doe"
    assert ce.agent_phone_number == "+15550001111"
    assert isinstance(ce.timeline, list)
    assert ce.telephony_duration == 91.5
    assert ce.telephony_start is not None
    assert ce.telephony_end is not None
    assert ce.raw_payload_json == payload


def test_create_call_event_dedupes_on_replay(session):
    call_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "call_status": "completed"}
    ce1 = _create_call_event(session, call_id, payload, "completed")
    ce2 = _create_call_event(session, call_id, payload, "completed")
    assert ce1.id == ce2.id


def test_create_call_event_refreshes_stale_in_progress_row(session):
    """spec/29: a call that first arrived as 'in-progress' and later gets its
    real terminal outcome must have the existing row overwritten in place."""
    call_id = str(uuid.uuid4())
    ce1 = _create_call_event(
        session, call_id,
        {"call_id": call_id, "Status": "in-progress", "duration": 0, "transcript": ""},
        "in-progress",
    )
    assert ce1.status == "in-progress"

    ce2 = _create_call_event(
        session, call_id,
        {
            "call_id": call_id,
            "call_status": "hangup_on_voicemail",
            "end_call_reason": "voicemail",
            "duration": 58,
            "transcript": "human: leave a message",
            "recording_url": "https://example.com/rec.mp3",
        },
        "hangup_on_voicemail",
    )
    assert ce2.id == ce1.id
    assert ce2.status == "hangup_on_voicemail"
    assert ce2.end_call_reason == "voicemail"
    assert ce2.duration_seconds == 58
    assert ce2.transcript == "human: leave a message"
    assert ce2.recording_url == "https://example.com/rec.mp3"


def test_create_call_event_does_not_overwrite_terminal_row(session):
    """A replay of an already-terminal row stays a true no-op dedupe."""
    call_id = str(uuid.uuid4())
    ce1 = _create_call_event(
        session, call_id,
        {"call_id": call_id, "call_status": "completed", "transcript": "real convo"},
        "completed",
    )
    ce2 = _create_call_event(
        session, call_id,
        {"call_id": call_id, "call_status": "hangup_on_voicemail", "transcript": "different"},
        "hangup_on_voicemail",
    )
    assert ce2.id == ce1.id
    assert ce2.status == "completed"
    assert ce2.transcript == "real convo"


def test_create_call_event_refreshes_queue_status_too(session):
    call_id = str(uuid.uuid4())
    _create_call_event(session, call_id, {"call_id": call_id, "Status": "queue"}, "queue")
    ce = _create_call_event(
        session, call_id,
        {"call_id": call_id, "call_status": "no-answer"},
        "no-answer",
    )
    assert ce.status == "no-answer"


def test_create_call_event_synthflow_fields_null_when_absent(session):
    call_id = str(uuid.uuid4())
    ce = _create_call_event(session, call_id, {"call_id": call_id}, "completed")
    assert ce.model_id is None
    assert ce.lead_name is None
    assert ce.timeline is None
    assert ce.telephony_duration is None


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_outbound_campaign — trust Cora's launch record over Synthflow's
# self-reported Agent/campaign_name, which is unreliable now that New Lead
# and Cold Lead share one physical workflow (2026-08-24 fix)
# ─────────────────────────────────────────────────────────────────────────────

def _make_launch_job(session: Session, contact_id: str, campaign_name: str,
                      status: str = "completed") -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        status=status,
        run_at=datetime.now(tz=timezone.utc),
        payload_json={"contact_id": contact_id, "campaign_name": campaign_name},
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()
    return job


def test_create_call_event_outbound_uses_launch_job_campaign_not_synthflow(session):
    """
    Regression test for the 2026-08-24 Cold Lead misattribution bug: a real
    Cold Lead outbound call whose Synthflow completion webhook self-reports
    Agent="...NewLead..." / campaign_name="New Lead" must still be recorded
    as Cold Lead, because that's what Cora's own launch record says was
    actually used to place the call.
    """
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_launch_job(session, contact_id, "Cold Lead")

    call_id = str(uuid.uuid4())
    payload = {
        "call_id": call_id,
        "call_status": "completed",
        "direction": "outbound",
        "contact_id": contact_id,
        "Agent": "Cora Outbound NewLead Completed Call",
        "campaign_name": "New Lead",  # Synthflow's self-reported (wrong) value
    }
    ce = _create_call_event(session, call_id, payload, "completed")

    assert ce.campaign_name == "Cold Lead"
    assert ce.voice_agent == "ColdLead"


def test_create_call_event_outbound_falls_back_when_no_launch_job(session):
    """No matching launch record (e.g. calls placed via the standalone test
    script, bypassing the job queue) — falls back to the payload's own
    self-reported value rather than storing nothing."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    call_id = str(uuid.uuid4())
    payload = {
        "call_id": call_id,
        "call_status": "completed",
        "direction": "outbound",
        "contact_id": contact_id,
        "Agent": "Cora Outbound NewLead Completed Call",
        "campaign_name": "New Lead",
    }
    ce = _create_call_event(session, call_id, payload, "completed")

    assert ce.campaign_name == "New Lead"
    assert ce.voice_agent == "NewLead"


def test_create_call_event_inbound_unaffected_by_launch_job_resolution(session):
    """Inbound calls have their own dedicated model/workflow and must keep
    using the payload's self-reported campaign_name — resolution only
    applies to outbound calls, even if a launch job happens to exist for
    the same contact_id."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    _make_launch_job(session, contact_id, "Cold Lead")

    call_id = str(uuid.uuid4())
    payload = {
        "call_id": call_id,
        "call_status": "completed",
        "direction": "inbound",
        "contact_id": contact_id,
        "Agent": "Cora Inbound - Completed Call",
        "campaign_name": "Inbound",
    }
    ce = _create_call_event(session, call_id, payload, "completed")

    assert ce.campaign_name == "Inbound"
    assert ce.voice_agent == "Inbound"


# ─────────────────────────────────────────────────────────────────────────────
# 17–20: process_call_event integration (mocked DB session)
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_job(payload: dict, job_id: str = "job-1") -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.payload_json = payload
    job.status = "pending"
    return job


def test_process_call_event_voicemail_routes_correctly(session):
    """Synthflow hangup_on_voicemail creates CallEvent + schedules voicemail job."""
    from app.worker.jobs.call_processing import process_call_event

    call_id = str(uuid.uuid4())
    payload = {
        "call_id": call_id,
        "call_status": "hangup_on_voicemail",
        "end_call_reason": "voicemail",
        "transcript": "",
    }
    job = _make_mock_job(payload)

    with (
        patch("app.worker.jobs.call_processing.get_sync_session") as mock_sess_ctx,
        patch("app.worker.jobs.call_processing.claim_job", return_value=job),
        patch("app.worker.jobs.call_processing.mark_running"),
        patch("app.worker.jobs.call_processing.complete_job"),
        patch("app.worker.jobs.call_processing.fail_job"),
        patch("app.worker.jobs.call_processing._create_call_event") as mock_create,
        patch("app.worker.jobs.call_processing._route_to_voicemail") as mock_vm,
        patch("app.worker.jobs.call_processing._route_to_call_through") as mock_ct,
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.call_processing.get_settings"),
    ):
        mock_sess_ctx.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_create.return_value = MagicMock(id="ce-1")

        process_call_event("job-1")

        mock_create.assert_called_once()
        mock_vm.assert_called_once()
        mock_ct.assert_not_called()


def test_process_call_event_completed_routes_to_call_through(session):
    from app.worker.jobs.call_processing import process_call_event

    call_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "call_status": "completed", "transcript": "Hi"}
    job = _make_mock_job(payload)

    with (
        patch("app.worker.jobs.call_processing.get_sync_session") as mock_sess_ctx,
        patch("app.worker.jobs.call_processing.claim_job", return_value=job),
        patch("app.worker.jobs.call_processing.mark_running"),
        patch("app.worker.jobs.call_processing.complete_job"),
        patch("app.worker.jobs.call_processing.fail_job"),
        patch("app.worker.jobs.call_processing._create_call_event") as mock_create,
        patch("app.worker.jobs.call_processing._route_to_voicemail") as mock_vm,
        patch("app.worker.jobs.call_processing._route_to_call_through") as mock_ct,
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.call_processing.get_settings"),
    ):
        mock_sess_ctx.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_create.return_value = MagicMock(id="ce-2")

        process_call_event("job-1")

        mock_create.assert_called_once()
        mock_ct.assert_called_once()
        mock_vm.assert_not_called()


def test_process_call_event_missing_call_id_fails_job():
    from app.worker.jobs.call_processing import process_call_event

    payload = {}  # no call_id
    job = _make_mock_job(payload)

    with (
        patch("app.worker.jobs.call_processing.get_sync_session") as mock_sess_ctx,
        patch("app.worker.jobs.call_processing.claim_job", return_value=job),
        patch("app.worker.jobs.call_processing.mark_running"),
        patch("app.worker.jobs.call_processing.complete_job"),
        patch("app.worker.jobs.call_processing.fail_job") as mock_fail,
        patch("app.worker.jobs.call_processing.create_exception"),
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.call_processing.get_settings"),
    ):
        mock_sess_ctx.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(ValueError, match="Missing call_id"):
            process_call_event("job-1")

        mock_fail.assert_called_once()


def test_process_call_event_unknown_status_defaults_to_call_through():
    """Unknown status must NOT crash the worker — it should warn and route to call-through."""
    from app.worker.jobs.call_processing import process_call_event

    call_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "call_status": "totally_unknown"}
    job = _make_mock_job(payload)

    with (
        patch("app.worker.jobs.call_processing.get_sync_session") as mock_sess_ctx,
        patch("app.worker.jobs.call_processing.claim_job", return_value=job),
        patch("app.worker.jobs.call_processing.mark_running"),
        patch("app.worker.jobs.call_processing.complete_job") as mock_complete,
        patch("app.worker.jobs.call_processing.fail_job") as mock_fail,
        patch("app.worker.jobs.call_processing.create_exception") as mock_exc,
        patch("app.worker.jobs.call_processing._create_call_event") as mock_create,
        patch("app.worker.jobs.call_processing._route_to_call_through") as mock_ct,
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.call_processing.get_settings"),
    ):
        mock_sess_ctx.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_create.return_value = MagicMock(id="ce-3")

        # Must not raise
        process_call_event("job-1")

        # Job should complete, not fail
        mock_complete.assert_called_once()
        mock_fail.assert_not_called()
        # Warning exception record created for operator visibility
        mock_exc.assert_called_once()
        # Defaulted to call-through path
        mock_ct.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21–27: Expanded voicemail status variants
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_left_voicemail():
    result = normalize_synthflow_outcome({"call_status": "left_voicemail"})
    assert result == "left_voicemail"


def test_normalize_voicemail_detected():
    result = normalize_synthflow_outcome({"call_status": "voicemail_detected"})
    assert result == "voicemail_detected"


def test_normalize_machine_detected():
    result = normalize_synthflow_outcome({"call_status": "machine_detected"})
    assert result == "machine_detected"


@pytest.mark.parametrize("vm_status", [
    "left_voicemail",
    "voicemail_detected",
    "machine_detected",
])
def test_process_call_event_voicemail_variants_route_to_voicemail(vm_status):
    """All expanded voicemail statuses must route to _route_to_voicemail."""
    from app.worker.jobs.call_processing import process_call_event

    call_id = str(uuid.uuid4())
    payload = {"call_id": call_id, "call_status": vm_status}
    job = _make_mock_job(payload)

    with (
        patch("app.worker.jobs.call_processing.get_sync_session") as mock_sess_ctx,
        patch("app.worker.jobs.call_processing.claim_job", return_value=job),
        patch("app.worker.jobs.call_processing.mark_running"),
        patch("app.worker.jobs.call_processing.complete_job"),
        patch("app.worker.jobs.call_processing.fail_job"),
        patch("app.worker.jobs.call_processing._create_call_event") as mock_create,
        patch("app.worker.jobs.call_processing._route_to_voicemail") as mock_vm,
        patch("app.worker.jobs.call_processing._route_to_call_through") as mock_ct,
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.call_processing.get_settings"),
    ):
        mock_sess_ctx.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_create.return_value = MagicMock(id=f"ce-{vm_status}")

        process_call_event("job-1")

        mock_vm.assert_called_once()
        mock_ct.assert_not_called()


def test_voicemail_statuses_constant_has_all_five_variants():
    """VOICEMAIL_STATUSES must include all five recognised variants."""
    expected = {"voicemail", "hangup_on_voicemail", "left_voicemail",
                "voicemail_detected", "machine_detected"}
    assert expected == VOICEMAIL_STATUSES


def test_user_canceled_is_a_failed_status():
    """
    Synthflow sends 'user-canceled' (not 'cancelled') when the lead hangs up
    before the call connects. Must be treated as a terminal failure (no AI
    processing, no operator exception) — not fall through to unknown_call_status.
    """
    assert "user-canceled" in _FAILED_STATUSES
