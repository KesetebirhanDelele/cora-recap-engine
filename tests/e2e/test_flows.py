"""
E2E flow tests — production-faithful multi-step scenario tests.

Validates system state AS-IS after real job execution using:
  - SQLite in-memory DB (same engine used by unit tests)
  - Real claim/complete/fail/exception logic (no mocking of job lifecycle)
  - Patched get_sync_session → injects test session into job functions
  - Patched get_settings → returns production-equivalent mock settings
  - Patched Redis queue constructors → None (no real Redis required)
  - Patched GHLClient → captures writes without making HTTP calls

Scenarios:
  SC1  New Lead — first voicemail (tier: None → 0, retry scheduled)
  SC2  New Lead — second voicemail (tier: 0 → 1, retry scheduled)
  SC3  New Lead — third voicemail (tier: 1 → 2, retry scheduled)
  SC4  New Lead — terminal voicemail (tier: 2 → 3, no retry, GHL write)
  SC5  Cold Lead — first voicemail (tier: None → 0, cold delay applied)
  SC6  Not Interested — lead closed (status: active → closed)
  SC7  Cold Lead Reactivation (re_engaged) — EXPECTED FAIL (known gap: no handler)
  SC8  Enrollment — campaign terminated (status: enrolled, tier: 3)
  SC9  Callback with time — outbound call scheduled at future time
  SC10 Campaign switch — New Lead + interested_not_now → Cold Lead + nurture

Hard rules enforced in this file:
  - NO application logic changes
  - NO fake timing / time.sleep
  - SC7 is run and its failure is asserted — gap is reported, not worked around
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.base import Base
from tests.e2e.helpers import (
    assert_call_event_created,
    assert_do_not_call,
    assert_job_status,
    assert_lead_campaign,
    assert_lead_status,
    assert_lead_tier,
    assert_no_do_not_call,
    assert_no_pending_outbound_call,
    assert_pending_outbound_call,
    find_job,
    get_exceptions,
    get_lead,
)
from tests.e2e.seed import (
    seed_call_event,
    seed_cold_lead,
    seed_new_lead,
    seed_process_call_event_job,
    seed_process_voicemail_tier_job,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(
    new_vm_tier_none: int = 120,
    new_vm_tier_0: int = 2880,
    new_vm_tier_1: int = 2880,
    new_vm_tier_2_finalize: bool = True,
    cold_vm_tier_none: int = 120,
    cold_vm_tier_0: int = 2880,
    cold_vm_tier_1: int = 2880,
    cold_vm_tier_2_finalizes: bool = True,
    nurture_delay_days: int = 7,
    sms_followup_delay_minutes: int = 30,
    email_followup_delay_days: int = 1,
) -> MagicMock:
    """
    Return a MagicMock settings object with production-equivalent values.

    validate_for_new_lead_vm_policy() is a real callable that inspects the
    mock attributes — it must be a real method, not a MagicMock, because
    the production code calls it and checks specific fields.
    """
    s = MagicMock()
    # Redis (disabled in tests)
    s.redis_url = ""
    s.redis_host = "localhost"
    s.redis_port = 6379
    s.redis_db = 0
    s.redis_username = ""
    s.redis_password = ""
    s.rq_default_queue = "default"
    s.rq_ai_queue = "ai"
    # GHL (shadow mode in tests)
    s.ghl_writes_enabled = False
    s.ghl_write_mode = "shadow"
    s.ghl_write_shadow_log_only = True
    s.ghl_field_ai_campaign = "AI Campaign"
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
    # Nurture / messaging delays
    s.nurture_delay_days = nurture_delay_days
    s.sms_followup_delay_minutes = sms_followup_delay_minutes
    s.email_followup_delay_days = email_followup_delay_days

    # validate_for_new_lead_vm_policy must be a real callable (not MagicMock)
    # because production code inspects specific attributes on 's' when it runs.
    # Bind as a no-op: all new_vm_tier_* fields are set, so no ConfigError.
    def _validate_new_lead_vm_policy():
        pass
    s.validate_for_new_lead_vm_policy = _validate_new_lead_vm_policy

    return s


@contextmanager
def _session_ctx(session: Session):
    """
    Return a context manager mock that yields the given session.

    Used to patch get_sync_session in job modules so jobs execute
    against the test session instead of a real Postgres connection.
    """
    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=session)
    mock.__exit__ = MagicMock(return_value=False)
    yield mock


def _make_session_patch(session: Session) -> MagicMock:
    """Build a MagicMock for get_sync_session that returns our test session."""
    mock_ctx_mgr = MagicMock()
    mock_ctx_mgr.__enter__ = MagicMock(return_value=session)
    mock_ctx_mgr.__exit__ = MagicMock(return_value=False)
    mock_fn = MagicMock(return_value=mock_ctx_mgr)
    return mock_fn


def _run_process_call_event(session: Session, job_id: str, settings: MagicMock) -> None:
    """Run process_call_event against the test session."""
    from app.worker.jobs.call_processing import process_call_event

    mock_session_fn = _make_session_patch(session)
    with (
        patch("app.worker.jobs.call_processing.get_sync_session", mock_session_fn),
        patch("app.worker.jobs.call_processing.get_settings", return_value=settings),
        patch("app.worker.jobs.call_processing.get_worker_id", return_value="test-worker"),
        patch("app.worker.jobs.call_processing._make_default_queue", return_value=None),
        patch("app.worker.jobs.call_processing._make_ai_queue", return_value=None),
    ):
        process_call_event(job_id)


def _run_process_voicemail_tier(
    session: Session, job_id: str, settings: MagicMock
) -> None:
    """Run process_voicemail_tier against the test session."""
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    mock_session_fn = _make_session_patch(session)
    with (
        patch("app.worker.jobs.voicemail_jobs.get_sync_session", mock_session_fn),
        patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=settings),
        patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="test-worker"),
        patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None),
        patch("app.adapters.ghl.GHLClient") as mock_ghl_cls,
    ):
        mock_ghl_cls.return_value.update_contact_fields.return_value = {}
        process_voicemail_tier(job_id)


def _run_voicemail_flow(
    session: Session,
    contact_id: str,
    call_status: str = "voicemail",
    campaign_name: str = "New Lead",
    transcript: str | None = None,
    settings: MagicMock | None = None,
) -> tuple[str, str]:
    """
    Run the full voicemail path end-to-end:
      seed process_call_event job → run it → find process_voicemail_tier job → run it.

    Returns (call_event_job_id, voicemail_tier_job_id).
    """
    settings = settings or _make_settings()

    # Seed initial job
    job = seed_process_call_event_job(
        session,
        contact_id=contact_id,
        call_status=call_status,
        campaign_name=campaign_name,
        transcript=transcript,
    )
    call_event_job_id = job.id

    # Run process_call_event — creates CallEvent + schedules process_voicemail_tier
    _run_process_call_event(session, call_event_job_id, settings)

    # Find the downstream voicemail tier job
    vm_job = find_job(session, job_type="process_voicemail_tier", status="pending")
    assert vm_job is not None, (
        "process_call_event did not schedule a process_voicemail_tier job. "
        "Check call_status routing."
    )
    vm_job_id = vm_job.id

    # Run process_voicemail_tier
    _run_process_voicemail_tier(session, vm_job_id, settings)

    return call_event_job_id, vm_job_id


# ---------------------------------------------------------------------------
# SC1: New Lead — first voicemail (tier: None → 0)
# ---------------------------------------------------------------------------

class TestSC1NewLeadFirstVoicemail:
    """
    Input:  New Lead with tier=None receives first voicemail webhook.
    Expect: tier advances to '0', pending launch_outbound_call scheduled.
    """

    def test_tier_advances_to_0(self, session):
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings()
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        assert_lead_tier(session, lead.contact_id, "0")

    def test_pending_outbound_call_scheduled(self, session):
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings()
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        assert_pending_outbound_call(session, lead.contact_id)

    def test_call_event_row_created(self, session):
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings()
        call_id_holder: list[str] = []
        orig_seed = seed_process_call_event_job

        job = seed_process_call_event_job(
            session, contact_id=lead.contact_id, call_status="voicemail",
            campaign_name="New Lead",
        )
        _run_process_call_event(session, job.id, settings)
        # Find call_id from job payload
        session.refresh(job)
        call_id = job.payload_json["call_id"]
        assert_call_event_created(session, call_id)

    def test_retry_delay_matches_new_lead_tier_none_setting(self, session):
        """Retry job run_at must be ~120 min from now (new_vm_tier_none default)."""
        from datetime import timedelta
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings(new_vm_tier_none=120)
        before = datetime.now(tz=timezone.utc)
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        job = assert_pending_outbound_call(session, lead.contact_id)
        run_at = job.run_at
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        assert run_at >= before + timedelta(minutes=119), (
            f"Expected run_at ≥ now+119min, got run_at={run_at}"
        )
        assert run_at <= before + timedelta(minutes=121), (
            f"Expected run_at ≤ now+121min, got run_at={run_at}"
        )


# ---------------------------------------------------------------------------
# SC2: New Lead — second voicemail (tier: 0 → 1)
# ---------------------------------------------------------------------------

class TestSC2NewLeadSecondVoicemail:
    """
    Input:  New Lead with tier='0' receives second voicemail.
    Expect: tier advances to '1', new retry job scheduled.
    """

    def test_tier_advances_from_0_to_1(self, session):
        lead = seed_new_lead(session, tier="0")
        settings = _make_settings()
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        assert_lead_tier(session, lead.contact_id, "1")

    def test_pending_outbound_call_scheduled(self, session):
        lead = seed_new_lead(session, tier="0")
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead")
        assert_pending_outbound_call(session, lead.contact_id)


# ---------------------------------------------------------------------------
# SC3: New Lead — third voicemail (tier: 1 → 2)
# ---------------------------------------------------------------------------

class TestSC3NewLeadThirdVoicemail:
    """
    Input:  New Lead with tier='1' receives third voicemail.
    Expect: tier advances to '2', retry job scheduled (not yet terminal).
    """

    def test_tier_advances_from_1_to_2(self, session):
        lead = seed_new_lead(session, tier="1")
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead")
        assert_lead_tier(session, lead.contact_id, "2")

    def test_pending_outbound_call_scheduled(self, session):
        lead = seed_new_lead(session, tier="1")
        settings = _make_settings(new_vm_tier_2_finalize=False)
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        # With finalize=False at tier 1, a retry is expected for the tier 1→2 advancement
        assert_pending_outbound_call(session, lead.contact_id)


# ---------------------------------------------------------------------------
# SC4: New Lead — terminal voicemail (tier: 2 → 3)
# ---------------------------------------------------------------------------

class TestSC4NewLeadTerminalVoicemail:
    """
    Input:  New Lead with tier='2', new_vm_tier_2_finalize=True.
    Expect: tier=3, no retry job, GHL write executed (shadow-gated).
    """

    def test_tier_advances_to_3(self, session):
        lead = seed_new_lead(session, tier="2")
        settings = _make_settings(new_vm_tier_2_finalize=True)
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        assert_lead_tier(session, lead.contact_id, "3")

    def test_no_retry_job_at_terminal_tier(self, session):
        lead = seed_new_lead(session, tier="2")
        settings = _make_settings(new_vm_tier_2_finalize=True)
        _run_voicemail_flow(session, lead.contact_id, campaign_name="New Lead",
                            settings=settings)
        assert_no_pending_outbound_call(session, lead.contact_id)

    def test_ghl_finalization_write_called(self, session):
        """GHL update_contact_fields must be called with AI Campaign=No at terminal tier."""
        lead = seed_new_lead(session, tier="2")
        settings = _make_settings(new_vm_tier_2_finalize=True)

        job = seed_process_voicemail_tier_job(
            session, contact_id=lead.contact_id, campaign_name="New Lead"
        )
        mock_session_fn = _make_session_patch(session)
        with (
            patch("app.worker.jobs.voicemail_jobs.get_sync_session", mock_session_fn),
            patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=settings),
            patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="test-worker"),
            patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None),
            patch("app.adapters.ghl.GHLClient") as mock_ghl_cls,
        ):
            mock_ghl_cls.return_value.update_contact_fields.return_value = {}
            from app.worker.jobs.voicemail_jobs import process_voicemail_tier
            process_voicemail_tier(job.id)

        mock_ghl_cls.return_value.update_contact_fields.assert_called_once_with(
            contact_id=lead.contact_id,
            field_updates={"AI Campaign": "No"},
        )


# ---------------------------------------------------------------------------
# SC5: Cold Lead — first voicemail (tier: None → 0, cold delay)
# ---------------------------------------------------------------------------

class TestSC5ColdLeadFirstVoicemail:
    """
    Input:  Cold Lead with tier=None receives first voicemail.
    Expect: tier='0', retry job scheduled with cold_vm_tier_none delay (120 min).
    """

    def test_tier_advances_to_0(self, session):
        lead = seed_cold_lead(session, tier=None)
        settings = _make_settings()
        _run_voicemail_flow(session, lead.contact_id, campaign_name="Cold Lead",
                            settings=settings)
        assert_lead_tier(session, lead.contact_id, "0")

    def test_retry_delay_uses_cold_lead_setting(self, session):
        """Cold Lead retry must use cold_vm_tier_none (120 min), not new_vm_tier_none."""
        from datetime import timedelta
        lead = seed_cold_lead(session, tier=None)
        settings = _make_settings(new_vm_tier_none=5, cold_vm_tier_none=120)
        before = datetime.now(tz=timezone.utc)
        _run_voicemail_flow(session, lead.contact_id, campaign_name="Cold Lead",
                            settings=settings)
        job = assert_pending_outbound_call(session, lead.contact_id)
        run_at = job.run_at
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        # Must be ~120 min (cold setting), NOT ~5 min (new lead setting)
        assert run_at >= before + timedelta(minutes=119), (
            f"Expected cold delay ≥119min but run_at={run_at} before={before}"
        )


# ---------------------------------------------------------------------------
# SC6: Not Interested — lead closed
# ---------------------------------------------------------------------------

class TestSC6NotInterested:
    """
    Input:  Lead says "no thanks not interested" in voicemail transcript.
    Expect: lead.status='closed', no pending outbound call, do_not_call=False.
    """

    def test_lead_status_closed(self, session):
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings()
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="no thanks not interested",
            settings=settings,
        )
        assert_lead_status(session, lead.contact_id, "closed")

    def test_no_pending_outbound_call_after_rejection(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="no thanks not interested",
        )
        assert_no_pending_outbound_call(session, lead.contact_id)

    def test_do_not_call_not_set(self, session):
        """not_interested closes lead but must NOT set do_not_call."""
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="I'm not interested",
        )
        assert_no_do_not_call(session, lead.contact_id)


# ---------------------------------------------------------------------------
# SC7: Cold Lead Reactivation (re_engaged) — KNOWN GAP / EXPECTED FAIL
# ---------------------------------------------------------------------------

class TestSC7ColdLeadReactivation:
    """
    Input:  Cold Lead says "actually I'm interested now" in voicemail transcript.
    Expect: campaign switches to 'New Lead', job completes cleanly, status unchanged.

    Gap GAP-01 is fixed: _handle_re_engaged added to _HANDLERS in intent_actions.py.
    """

    def _run_re_engaged(self, session, lead, transcript="actually I'm interested now"):
        call_event = seed_call_event(
            session,
            contact_id=lead.contact_id,
            status="voicemail",
            transcript=transcript,
        )
        job = seed_process_voicemail_tier_job(
            session,
            contact_id=lead.contact_id,
            call_event_id=call_event.id,
            campaign_name="Cold Lead",
        )
        _run_process_voicemail_tier(session, job.id, _make_settings())
        return job

    def test_campaign_switches_to_new_lead(self, session):
        """Cold Lead re_engaged → campaign_name must become 'New Lead'."""
        lead = seed_cold_lead(session, tier=None)
        self._run_re_engaged(session, lead)
        assert_lead_campaign(session, lead.contact_id, "New Lead")

    def test_job_completes_cleanly(self, session):
        """process_voicemail_tier must complete without error."""
        lead = seed_cold_lead(session, tier=None)
        job = self._run_re_engaged(session, lead)
        session.expire(job)
        assert_job_status(session, job.id, "completed")


# ---------------------------------------------------------------------------
# SC8: Enrollment — campaign terminated
# ---------------------------------------------------------------------------

class TestSC8Enrollment:
    """
    Input:  Lead says "I want to enroll" in voicemail transcript.
    Expect: status='enrolled', tier='3', GHL write (AI Campaign=No), no retry job.
    """

    def test_lead_status_enrolled(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="I want to enroll",
        )
        assert_lead_status(session, lead.contact_id, "enrolled")

    def test_tier_set_to_3_on_enrollment(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="I want to enroll",
        )
        assert_lead_tier(session, lead.contact_id, "3")

    def test_no_retry_job_after_enrollment(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="I want to enroll",
        )
        assert_no_pending_outbound_call(session, lead.contact_id)

    def test_ghl_ai_campaign_no_written_on_enrollment(self, session):
        """Enrollment must write AI Campaign=No to GHL (shadow-gated)."""
        lead = seed_new_lead(session, tier=None)
        settings = _make_settings()

        call_event = seed_call_event(
            session,
            contact_id=lead.contact_id,
            status="voicemail",
            transcript="I want to enroll",
        )
        job = seed_process_voicemail_tier_job(
            session,
            contact_id=lead.contact_id,
            call_event_id=call_event.id,
            campaign_name="New Lead",
        )

        mock_session_fn = _make_session_patch(session)
        with (
            patch("app.worker.jobs.voicemail_jobs.get_sync_session", mock_session_fn),
            patch("app.worker.jobs.voicemail_jobs.get_settings", return_value=settings),
            patch("app.worker.jobs.voicemail_jobs.get_worker_id", return_value="test-worker"),
            patch("app.worker.jobs.voicemail_jobs._make_default_queue", return_value=None),
            patch("app.adapters.ghl.GHLClient") as mock_ghl_cls,
        ):
            mock_ghl_cls.return_value.update_contact_fields.return_value = {}
            from app.worker.jobs.voicemail_jobs import process_voicemail_tier
            process_voicemail_tier(job.id)

        mock_ghl_cls.return_value.update_contact_fields.assert_called_once_with(
            contact_id=lead.contact_id,
            field_updates={"AI Campaign": "No"},
        )

    def test_enrollment_not_treated_as_campaign_switch(self, session):
        """Enrolled lead must have no campaign switch — enrollment is termination."""
        from app.core.campaigns import evaluate_campaign_switch
        assert evaluate_campaign_switch("New Lead", "enrolled") is None
        assert evaluate_campaign_switch("Cold Lead", "enrolled") is None


# ---------------------------------------------------------------------------
# SC9: Callback with time — outbound call scheduled at future time
# ---------------------------------------------------------------------------

class TestSC9CallbackWithTime:
    """
    Input:  Lead says "call me back tomorrow" in voicemail transcript.
    Expect: pending launch_outbound_call job scheduled ≥ 12h from now.
    """

    def test_callback_outbound_job_created(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="call me back tomorrow",
        )
        assert_pending_outbound_call(session, lead.contact_id)

    def test_callback_run_at_is_future(self, session):
        """Callback job run_at must be in the future (not immediate)."""
        from datetime import timedelta
        lead = seed_new_lead(session, tier=None)
        before = datetime.now(tz=timezone.utc)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="call me back tomorrow",
        )
        job = assert_pending_outbound_call(session, lead.contact_id)
        run_at = job.run_at
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        # "tomorrow" resolves to now + ~24h; must be well above fallback 2h
        assert run_at > before + timedelta(hours=12), (
            f"Expected run_at > 12h from now, got run_at={run_at}"
        )

    def test_lead_tier_unchanged_after_callback(self, session):
        """Callback intent must not advance voicemail tier."""
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="call me back tomorrow",
        )
        # Tier should remain None (intent handler short-circuits tier logic)
        assert_lead_tier(session, lead.contact_id, None)


# ---------------------------------------------------------------------------
# SC10: Campaign switch — New Lead + interested_not_now → Cold Lead + nurture
# ---------------------------------------------------------------------------

class TestSC10CampaignSwitch:
    """
    Input:  New Lead says "not right now maybe later" in voicemail transcript.
    Expect: campaign_name='Cold Lead', status='nurture', next_action_at set,
            no retry outbound call job.
    """

    def test_campaign_switches_to_cold_lead(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="not right now maybe later",
        )
        assert_lead_campaign(session, lead.contact_id, "Cold Lead")

    def test_lead_status_set_to_nurture(self, session):
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="not right now maybe later",
        )
        assert_lead_status(session, lead.contact_id, "nurture")

    def test_next_action_at_is_set(self, session):
        """next_action_at must be set by interested_not_now handler."""
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="not right now maybe later",
        )
        refreshed = get_lead(session, lead.contact_id)
        assert refreshed.next_action_at is not None, (
            "Expected next_action_at to be set for nurture lead"
        )

    def test_no_retry_outbound_after_nurture(self, session):
        """interested_not_now short-circuits tier logic — no retry outbound call."""
        lead = seed_new_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="New Lead",
            transcript="not right now maybe later",
        )
        assert_no_pending_outbound_call(session, lead.contact_id)

    def test_cold_lead_uncertain_does_not_switch_campaign(self, session):
        """Cold Lead expressing uncertainty must NOT switch campaign (stays cold)."""
        lead = seed_cold_lead(session, tier=None)
        _run_voicemail_flow(
            session, lead.contact_id,
            campaign_name="Cold Lead",
            transcript="not sure maybe",
        )
        assert_lead_campaign(session, lead.contact_id, "Cold Lead")
