"""
Unit tests for voicemail_jobs.py — settings-driven retry outbound call scheduling.

Covers:
  1.  _schedule_retry_outbound_call: New Lead tier=None → delay from new_vm_tier_none setting
  2.  _schedule_retry_outbound_call: New Lead tier='0' → delay from new_vm_tier_0 setting
  3.  _schedule_retry_outbound_call: New Lead tier='1' → delay from new_vm_tier_1 setting
  4.  _schedule_retry_outbound_call: New Lead tier='2' + finalize=True → no job
  5.  _schedule_retry_outbound_call: tier='3' → no entry in delay map, no job
  6.  _schedule_retry_outbound_call: Cold Lead → uses cold_vm_tier_* settings
  7.  _schedule_retry_outbound_call: settings delay=None (unset) → no job
  8.  _get_next_tier: canonical sequence None→0→1→2→3
  9.  _get_next_tier: terminal tier '3' returns None
  10. _get_next_tier: unknown tier returns None
  11. process_voicemail_tier: non-terminal path calls _schedule_retry_outbound_call
  12. process_voicemail_tier: terminal path does NOT call _schedule_retry_outbound_call
  13. process_voicemail_tier: already claimed returns immediately
  14. process_voicemail_tier: missing lead_state is auto-created → job proceeds
  15. process_voicemail_tier: empty contact_id raises ValueError → fails job
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.models.lead_state import LeadState
from app.worker.jobs.voicemail_jobs import _get_next_tier, _schedule_retry_outbound_call


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


def _make_settings(
    new_vm_tier_none=2,
    new_vm_tier_0=4,
    new_vm_tier_1=6,
    new_vm_tier_2_finalize=True,
    cold_vm_tier_none=120,
    cold_vm_tier_0=2880,
    cold_vm_tier_1=2880,
    cold_vm_tier_2_finalizes=True,
):
    s = MagicMock()
    s.redis_url = ""
    s.redis_host = "localhost"
    s.redis_port = 6379
    s.redis_db = 0
    s.redis_username = ""
    s.redis_password = ""
    s.rq_default_queue = "default"
    s.ghl_field_ai_campaign = "AI Campaign"
    s.ghl_writes_enabled = False
    # New Lead VM tier delays
    s.new_vm_tier_none_delay_minutes = new_vm_tier_none
    s.new_vm_tier_0_delay_minutes = new_vm_tier_0
    s.new_vm_tier_1_delay_minutes = new_vm_tier_1
    s.new_vm_tier_2_finalize = new_vm_tier_2_finalize
    # Cold Lead VM tier delays
    s.cold_vm_tier_none_delay_minutes = cold_vm_tier_none
    s.cold_vm_tier_0_delay_minutes = cold_vm_tier_0
    s.cold_vm_tier_1_delay_minutes = cold_vm_tier_1
    s.cold_vm_tier_2_finalizes = cold_vm_tier_2_finalizes
    return s


def _make_job(session: Session, payload: dict, status: str = "pending") -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_voicemail_tier",
        entity_type="call",
        entity_id=payload.get("call_id", "test-call"),
        status=status,
        run_at=datetime.now(tz=timezone.utc),
        payload_json=payload,
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()
    return job


def _make_lead(session: Session, contact_id: str, tier: str | None) -> LeadState:
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        ai_campaign_value=tier,
        lead_stage="New Lead",
        campaign_name="New_Lead",
        normalized_phone="+15550001234",
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(lead)
    session.flush()
    return lead


# ─────────────────────────────────────────────────────────────────────────────
# 1–7: _schedule_retry_outbound_call (settings-driven)
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_new_lead_tier_none_uses_settings_delay(session):
    """New Lead tier None → delay read from new_vm_tier_none_delay_minutes."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    call_id = f"call-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_none=2)

    scheduled = []

    def _fake_schedule_job(**kwargs):
        scheduled.append(kwargs)
        return MagicMock()

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job", side_effect=_fake_schedule_job):
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", call_id, None, "New Lead", settings)

    assert len(scheduled) == 1
    payload = scheduled[0]["payload"]
    assert payload["vm_retry_attempt"] == 1
    assert payload["delay_minutes"] == 2
    assert payload["contact_id"] == contact_id
    assert payload["phone_number"] == "+15550001234"
    assert payload["campaign_name"] == "New Lead"
    assert scheduled[0]["job_type"] == "launch_outbound_call"


def test_retry_new_lead_tier_0_uses_settings_delay(session):
    """New Lead tier '0' → delay read from new_vm_tier_0_delay_minutes."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_0=4)

    scheduled = []

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job",
               side_effect=lambda **kw: scheduled.append(kw) or MagicMock()):
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", "0", "New Lead", settings)

    assert len(scheduled) == 1
    assert scheduled[0]["payload"]["vm_retry_attempt"] == 2
    assert scheduled[0]["payload"]["delay_minutes"] == 4


def test_retry_new_lead_tier_1_uses_settings_delay(session):
    """New Lead tier '1' → delay read from new_vm_tier_1_delay_minutes."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_1=6)

    scheduled = []

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job",
               side_effect=lambda **kw: scheduled.append(kw) or MagicMock()):
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", "1", "New Lead", settings)

    assert len(scheduled) == 1
    assert scheduled[0]["payload"]["vm_retry_attempt"] == 3
    assert scheduled[0]["payload"]["delay_minutes"] == 6


def test_retry_new_lead_tier_2_stops_when_finalize_true(session):
    """New Lead tier '2' + new_vm_tier_2_finalize=True → no retry job."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_2_finalize=True)

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job") as mock_sched:
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", "2", "New Lead", settings)
    mock_sched.assert_not_called()


def test_retry_tier_3_no_entry_in_map(session):
    """Tier '3' has no entry in the delay map → no retry job."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings()

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job") as mock_sched:
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", "3", "New Lead", settings)
    mock_sched.assert_not_called()


def test_retry_cold_lead_uses_cold_vm_settings(session):
    """Cold Lead uses cold_vm_tier_*_delay_minutes, not new_vm_tier_*."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_none=2, cold_vm_tier_none=120)

    scheduled = []

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job",
               side_effect=lambda **kw: scheduled.append(kw) or MagicMock()):
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", None, "Cold Lead", settings)

    assert len(scheduled) == 1
    assert scheduled[0]["payload"]["delay_minutes"] == 120  # cold setting, not 2


def test_retry_none_delay_in_settings_skips(session):
    """If the settings value is None (unset), no job is scheduled."""
    contact_id = f"c-{uuid.uuid4().hex[:6]}"
    settings = _make_settings(new_vm_tier_none=None)

    with patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None), \
         patch("app.worker.jobs.voicemail_jobs.schedule_job") as mock_sched:
        _schedule_retry_outbound_call(session, contact_id, "+15550001234", "call-x", None, "New Lead", settings)
    mock_sched.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 8–10: _get_next_tier
# ─────────────────────────────────────────────────────────────────────────────

def test_get_next_tier_canonical_sequence():
    assert _get_next_tier(None) == "0"
    assert _get_next_tier("0") == "1"
    assert _get_next_tier("1") == "2"
    assert _get_next_tier("2") == "3"


def test_get_next_tier_terminal_returns_none():
    assert _get_next_tier("3") is None


def test_get_next_tier_unknown_returns_none():
    assert _get_next_tier("99") is None
    assert _get_next_tier("bad") is None


# ─────────────────────────────────────────────────────────────────────────────
# 11–14: process_voicemail_tier integration
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_job_obj(payload: dict, job_id: str = "job-vm-1") -> MagicMock:
    job = MagicMock()
    job.id = job_id
    job.payload_json = payload
    job.status = "pending"
    return job


def test_process_voicemail_tier_non_terminal_calls_retry_scheduler(session):
    """Non-terminal tier advancement must call _schedule_retry_outbound_call."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    contact_id = f"c-vm-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id, tier=None)  # first voicemail
    job = _make_mock_job_obj({"call_id": "call-vm-1", "contact_id": contact_id})

    mock_policy = MagicMock()
    mock_policy.is_terminal = False
    mock_policy.schedule_synthflow_callback = True
    mock_policy.delay_minutes = 120
    mock_policy.campaign_name = "New_Lead"
    mock_policy.next_tier = "0"

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.voicemail_jobs.mark_running"),
        patch("app.worker.jobs.voicemail_jobs.complete_job"),
        patch("app.worker.jobs.voicemail_jobs.fail_job"),
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
        patch("app.services.tier_policy.get_tier_policy", return_value=mock_policy),
        patch("app.worker.jobs.voicemail_jobs._advance_tier"),
        patch("app.worker.jobs.voicemail_jobs._schedule_retry_outbound_call") as mock_retry,
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        process_voicemail_tier("job-vm-1")

    mock_retry.assert_called_once()


def test_process_voicemail_tier_terminal_skips_retry_scheduler(session):
    """Terminal tier must NOT call _schedule_retry_outbound_call."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    contact_id = f"c-term-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id, tier="2")  # one step from terminal
    job = _make_mock_job_obj({"call_id": "call-term-1", "contact_id": contact_id})

    mock_policy = MagicMock()
    mock_policy.is_terminal = True
    mock_policy.schedule_synthflow_callback = False

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.voicemail_jobs.mark_running"),
        patch("app.worker.jobs.voicemail_jobs.complete_job"),
        patch("app.worker.jobs.voicemail_jobs.fail_job"),
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
        patch("app.services.tier_policy.get_tier_policy", return_value=mock_policy),
        patch("app.worker.jobs.voicemail_jobs._advance_tier"),
        patch("app.worker.jobs.voicemail_jobs._finalize_campaign"),
        patch("app.worker.jobs.voicemail_jobs._schedule_retry_outbound_call") as mock_retry,
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        process_voicemail_tier("job-vm-1")

    mock_retry.assert_not_called()


def test_process_voicemail_tier_already_at_terminal_completes_cleanly(session):
    """
    Lead already at tier '3' — job completes without error, no retry scheduled,
    no exception record created.  This covers the RQ retry case where the job
    fires again after finalization already ran.
    """
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    contact_id = f"c-t3-{uuid.uuid4().hex[:6]}"
    _make_lead(session, contact_id, tier="3")
    job = _make_mock_job_obj({"call_id": "call-t3-1", "contact_id": contact_id})

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.voicemail_jobs.mark_running"),
        patch("app.worker.jobs.voicemail_jobs.complete_job") as mock_complete,
        patch("app.worker.jobs.voicemail_jobs.fail_job") as mock_fail,
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
        patch("app.worker.jobs.voicemail_jobs.create_exception") as mock_exc,
        patch("app.worker.jobs.voicemail_jobs._schedule_retry_outbound_call") as mock_retry,
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        process_voicemail_tier("job-t3-1")

    mock_complete.assert_called_once()
    mock_fail.assert_not_called()
    mock_exc.assert_not_called()
    mock_retry.assert_not_called()


def test_process_voicemail_tier_already_claimed_returns(session):
    """Already-claimed job → exits immediately without error."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=None),
        patch("app.worker.jobs.voicemail_jobs.mark_running") as mock_mark,
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        process_voicemail_tier("job-missing")  # must not raise

    mock_mark.assert_not_called()


def test_process_voicemail_tier_missing_lead_state_is_auto_created(session):
    """No lead_state row → one is created automatically; job proceeds normally."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier
    from sqlalchemy import select
    from app.models.lead_state import LeadState

    contact_id = f"c-auto-{uuid.uuid4().hex[:6]}"
    # Do NOT pre-create a LeadState row
    job = _make_mock_job_obj({"call_id": "call-auto", "contact_id": contact_id})

    mock_policy = MagicMock()
    mock_policy.is_terminal = False
    mock_policy.schedule_synthflow_callback = False  # skip callback scheduling

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.voicemail_jobs.mark_running"),
        patch("app.worker.jobs.voicemail_jobs.complete_job"),
        patch("app.worker.jobs.voicemail_jobs.fail_job") as mock_fail,
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
        patch("app.services.tier_policy.get_tier_policy", return_value=mock_policy),
        patch("app.worker.jobs.voicemail_jobs._advance_tier"),
        patch("app.worker.jobs.voicemail_jobs._schedule_retry_outbound_call"),
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        process_voicemail_tier("job-auto")

    mock_fail.assert_not_called()
    # Row must have been flushed into the session
    created = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert created is not None
    assert created.ai_campaign_value is None
    assert created.version == 0


def test_process_voicemail_tier_empty_contact_id_fails_job(session):
    """Empty contact_id (no lead to attach tier to) → ValueError → job fails."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    job = _make_mock_job_obj({"call_id": "call-nocontact", "contact_id": ""})

    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session") as mock_ctx,
        patch("app.worker.jobs.voicemail_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.voicemail_jobs.mark_running"),
        patch("app.worker.jobs.voicemail_jobs.complete_job"),
        patch("app.worker.jobs.voicemail_jobs.fail_job") as mock_fail,
        patch("app.worker.jobs.voicemail_jobs.create_exception"),
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=_make_settings()),
    ):
        mock_ctx.return_value.__enter__ = lambda s, *a: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(ValueError, match="contact_id and phone are both empty"):
            process_voicemail_tier("job-nocontact")

    mock_fail.assert_called_once()
