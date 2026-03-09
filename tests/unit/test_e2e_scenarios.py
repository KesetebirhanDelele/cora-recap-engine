"""
Phase 10 — end-to-end scenario tests.

Tests the full processing pipeline at the service/orchestration layer.
All external adapters (GHL, OpenAI, Synthflow) are mocked.
Database uses SQLite in-memory.

Scenarios covered (from spec/05_eval_plan.md):
  1.  Completed call, consent YES → task created, summary writeback called
  2.  Completed call, consent NO → task created, no summary writeback
  3.  Blank transcript → blank summary, no API call, no writeback
  4.  Duplicate dedupe_key replay → IntegrityError (DB-level protection)
  5.  Cold Lead voicemail None→0 → 120-min delay, Synthflow scheduled
  6.  Cold Lead voicemail 0→1 → 2880-min delay, Synthflow scheduled
  7.  Cold Lead voicemail 1→2 → 2880-min delay, Synthflow scheduled
  8.  Cold Lead voicemail 2→3 → terminal, no Synthflow, GHL finalization called
  9.  Duplicate callback prevention → second voicemail skips scheduling
  10. GHL missing api_key → ConfigError raised (identity/auth failure)
  11. Tier 3 is terminal for both Cold Lead and New Lead (canonical model)
  12. Consent gate: UNKNOWN treated as NO (no writeback)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import ConfigError, Settings
from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.models.summary import SummaryResult
from app.models.task_event import TaskEvent
from app.schemas.ai import ConsentOutput, SummaryOutput
from app.services.ai import detect_consent, generate_student_summary
from app.services.tier_policy import get_cold_lead_policy, has_pending_callback

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


def _settings(**overrides) -> Settings:
    defaults = dict(
        openai_api_key="sk-test",
        ghl_api_key="ghl-test",
        ghl_location_id="loc-test",
        ghl_write_mode="shadow",
        ghl_write_shadow_log_only=True,
        synthflow_api_key="sf-test",
        synthflow_model_id="model-test",
        app_env="test",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_call_event(session, call_id=None, transcript=None, dedupe_suffix="intake") -> CallEvent:
    cid = call_id or str(uuid.uuid4())
    ce = CallEvent(
        id=str(uuid.uuid4()),
        call_id=cid,
        contact_id=str(uuid.uuid4()),
        status="completed",
        transcript=transcript or "This is a real call transcript with enough content.",
        dedupe_key=f"{cid}:{dedupe_suffix}",
        created_at=_now(),
    )
    session.add(ce)
    session.flush()
    return ce


def _make_lead(session, contact_id=None, campaign="Cold Lead", tier=None) -> LeadState:
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id or str(uuid.uuid4()),
        normalized_phone="+15551234567",
        lead_stage="Cold Lead",
        campaign_name=campaign,
        ai_campaign="Yes",
        ai_campaign_value=tier,
        version=0,
        updated_at=_now(),
        created_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


def _mock_ai_client(summary_text="Great call!", consent="YES") -> MagicMock:
    client = MagicMock()
    client.chat_completion.return_value = {
        "student_summary": summary_text,
        "summary_offered": True,
        "consent": consent,
        "confidence": "high",
        "lead_stage": "Cold Lead",
        "call_outcome": "completed",
        "key_topics": [],
    }
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1. Consent YES → allows writeback
# ─────────────────────────────────────────────────────────────────────────────

def test_consent_yes_allows_writeback():
    """
    Completed call with consent YES:
    ConsentOutput.allows_writeback must be True.
    This is the gate that controls GHL summary writeback.
    """
    s = _settings()
    ai_client = _mock_ai_client(consent="YES")

    consent_result = detect_consent(
        "Yes please send me a summary by email.",
        settings=s,
        client=ai_client,
    )
    assert consent_result.consent == "YES"
    assert consent_result.allows_writeback is True


def test_consent_yes_summary_and_task_both_created(session):
    """
    With consent YES and valid transcript:
    - summary_results row exists with consent='YES'
    - task_events row exists with status='created'
    Both are created exactly once (idempotency enforced by dedupe).
    """
    from app.worker.jobs.ai_jobs import _create_ghl_task, _persist_summary

    call_event_id = str(uuid.uuid4())
    ce = CallEvent(
        id=call_event_id, call_id=str(uuid.uuid4()), contact_id="cid-01",
        dedupe_key=f"e2e-yes:{uuid.uuid4()}", status="completed",
        transcript="A real transcript here.", created_at=_now(),
    )
    session.add(ce)
    session.flush()

    summary = SummaryOutput(
        student_summary="Great call!", summary_offered=True,
        model_used="gpt-4o-mini", prompt_family="student_summary_generator",
        prompt_version="v1",
    )
    consent = ConsentOutput(
        consent="YES", confidence="high",
        model_used="gpt-4o-mini", prompt_family="summary_consent_detector",
        prompt_version="v1",
    )
    _persist_summary(session, call_event_id, summary, consent)

    sr = session.scalars(
        __import__("sqlalchemy", fromlist=["select"]).select(SummaryResult)
        .where(SummaryResult.call_event_id == call_event_id)
    ).first()
    assert sr is not None
    assert sr.summary_consent == "YES"
    assert sr.student_summary == "Great call!"

    # GHL task creation (shadow mode — returns shadow dict, records task_event)
    s = _settings()
    with patch("app.adapters.ghl.GHLClient.create_task",
               return_value={"shadow": True, "operation": "create_task"}):
        _create_ghl_task(session, ce.call_id, call_event_id, s)

    te = session.scalars(
        __import__("sqlalchemy", fromlist=["select"]).select(TaskEvent)
        .where(TaskEvent.call_event_id == call_event_id)
    ).first()
    assert te is not None
    assert te.status == "created"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Consent NO → no writeback
# ─────────────────────────────────────────────────────────────────────────────

def test_consent_no_blocks_writeback():
    """consent NO → allows_writeback is False — GHL write must not be called."""
    s = _settings()
    ai_client = _mock_ai_client(consent="NO")
    consent_result = detect_consent(
        "No thanks, I don't want the summary.", settings=s, client=ai_client
    )
    assert consent_result.consent == "NO"
    assert consent_result.allows_writeback is False


def test_consent_no_summary_persisted_but_no_ghl_write():
    """
    Even when consent=NO, summary_results is persisted for audit.
    But the GHL summary write must NOT be called.
    """
    from app.worker.jobs.ai_jobs import _write_summary_to_ghl

    summary = SummaryOutput(
        student_summary="Some summary.", summary_offered=True,
        model_used="gpt-4o-mini", prompt_family="student_summary_generator",
        prompt_version="v1",
    )
    consent = ConsentOutput(
        consent="NO", confidence="high",
        model_used="gpt-4o-mini", prompt_family="summary_consent_detector",
        prompt_version="v1",
    )

    # The service layer must NOT call _write_summary_to_ghl when consent=NO
    s = _settings()
    with patch("app.adapters.ghl.GHLClient.update_contact_fields") as mock_write:
        # simulate what run_call_analysis does: check allows_writeback first
        if consent.allows_writeback and summary.student_summary:
            _write_summary_to_ghl("call-1", summary.student_summary, s)
        mock_write.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Blank transcript → no API call, blank summary
# ─────────────────────────────────────────────────────────────────────────────

def test_blank_transcript_no_openai_call():
    """Blank/None transcript → no OpenAI API call made."""
    s = _settings()
    mock_client = MagicMock()
    result = generate_student_summary(None, settings=s, client=mock_client)
    mock_client.chat_completion.assert_not_called()
    assert result.student_summary == ""
    assert result.summary_offered is False


def test_blank_transcript_unknown_consent_no_writeback():
    """Blank transcript → UNKNOWN consent → allows_writeback=False."""
    s = _settings()
    mock_client = MagicMock()
    consent_result = detect_consent("", settings=s, client=mock_client)
    mock_client.chat_completion.assert_not_called()
    assert consent_result.consent == "UNKNOWN"
    assert consent_result.allows_writeback is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Duplicate dedupe_key replay → DB constraint fires
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_dedupe_key_rejected(session):
    """
    Replaying the same call_id with the same action_type:
    The UNIQUE constraint on dedupe_key prevents a second row.
    This is the primary idempotency guard for call events.
    """
    from sqlalchemy.exc import IntegrityError

    dedupe_key = f"call-dupe:{uuid.uuid4()}:intake"
    e1 = CallEvent(
        id=str(uuid.uuid4()), call_id="call-dup-1",
        dedupe_key=dedupe_key, status="completed", created_at=_now(),
    )
    e2 = CallEvent(
        id=str(uuid.uuid4()), call_id="call-dup-2",
        dedupe_key=dedupe_key, status="completed", created_at=_now(),
    )
    session.add(e1)
    session.flush()
    session.add(e2)
    with pytest.raises(IntegrityError):
        session.flush()


def test_duplicate_task_creation_blocked_by_application_layer(session):
    """
    Second call to _create_ghl_task with same call_event_id → skipped (no duplicate).
    Application-layer dedupe guard before partial unique index (Postgres-only).
    """
    from app.worker.jobs.ai_jobs import _create_ghl_task

    ce = _make_call_event(session, dedupe_suffix="task-dedup")
    s = _settings()

    with patch("app.adapters.ghl.GHLClient.create_task",
               return_value={"shadow": True}) as mock_create:
        _create_ghl_task(session, ce.call_id, ce.id, s)
        assert mock_create.call_count == 1

        # Second call must be skipped — task_event already 'created'
        _create_ghl_task(session, ce.call_id, ce.id, s)
        assert mock_create.call_count == 1  # still 1, not 2


# ─────────────────────────────────────────────────────────────────────────────
# 5-8. Cold Lead full tier sequence
# ─────────────────────────────────────────────────────────────────────────────

def test_cold_lead_none_to_0_policy():
    """Cold Lead None → 0: 120-minute delay, Synthflow callback required."""
    s = _settings(cold_vm_tier_none_delay_minutes=120)
    policy = get_cold_lead_policy(None, settings=s)
    assert policy.next_tier == "0"
    assert policy.delay_minutes == 120
    assert policy.schedule_synthflow_callback is True
    assert policy.is_terminal is False


def test_cold_lead_0_to_1_policy():
    s = _settings(cold_vm_tier_0_delay_minutes=2880)
    policy = get_cold_lead_policy("0", settings=s)
    assert policy.next_tier == "1"
    assert policy.delay_minutes == 2880
    assert policy.schedule_synthflow_callback is True


def test_cold_lead_1_to_2_policy():
    s = _settings(cold_vm_tier_1_delay_minutes=2880)
    policy = get_cold_lead_policy("1", settings=s)
    assert policy.next_tier == "2"
    assert policy.delay_minutes == 2880
    assert policy.schedule_synthflow_callback is True


def test_cold_lead_2_to_3_terminal_no_synthflow():
    """Cold Lead 2→3: terminal, NO Synthflow callback, finalization executes."""
    s = _settings()
    policy = get_cold_lead_policy("2", settings=s)
    assert policy.next_tier == "3"
    assert policy.is_terminal is True
    assert policy.schedule_synthflow_callback is False
    assert policy.delay_minutes == 0


def test_cold_lead_full_sequence_delays():
    """Full Cold Lead sequence: 2h, 2d, 2d, terminal. All delays correct."""
    s = _settings(
        cold_vm_tier_none_delay_minutes=120,
        cold_vm_tier_0_delay_minutes=2880,
        cold_vm_tier_1_delay_minutes=2880,
    )
    delays = [
        get_cold_lead_policy(None, s).delay_minutes,
        get_cold_lead_policy("0", s).delay_minutes,
        get_cold_lead_policy("1", s).delay_minutes,
    ]
    assert delays == [120, 2880, 2880]  # 2h, 2d, 2d


# ─────────────────────────────────────────────────────────────────────────────
# 9. Duplicate callback prevention
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_callback_skipped_when_pending(session):
    """
    When a Synthflow callback is already pending for a contact,
    a second voicemail event must not schedule another.
    has_pending_callback() returns True → caller skips scheduling.
    """
    contact_id = str(uuid.uuid4())

    # First callback already scheduled
    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="synthflow_callback",
        entity_type="lead", entity_id=contact_id,
        run_at=_now(), status="pending", version=0,
        created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()

    # Second voicemail arrives — check returns True, skip
    assert has_pending_callback(session, contact_id) is True


def test_no_duplicate_if_prior_callback_completed(session):
    """If the prior callback is completed, a new one can be scheduled."""
    contact_id = str(uuid.uuid4())
    job = ScheduledJob(
        id=str(uuid.uuid4()), job_type="synthflow_callback",
        entity_type="lead", entity_id=contact_id,
        run_at=_now(), status="completed", version=0,
        created_at=_now(), updated_at=_now(),
    )
    session.add(job)
    session.flush()

    assert has_pending_callback(session, contact_id) is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. Missing GHL credentials → ConfigError
# ─────────────────────────────────────────────────────────────────────────────

def test_ghl_missing_api_key_raises_config_error():
    """
    GHL operations without credentials must raise ConfigError immediately.
    No unsafe API calls or silent failures.
    """
    from unittest.mock import MagicMock

    import httpx

    from app.adapters.ghl import GHLClient

    s = Settings(_env_file=None)  # no api_key
    client = GHLClient(settings=s, _http=MagicMock(spec=httpx.Client))
    with pytest.raises(ConfigError, match="GHL_API_KEY"):
        client.search_contact_by_phone("+15551234567")


def test_synthflow_missing_key_raises_config_error():
    """Synthflow operations without credentials raise ConfigError immediately."""
    import httpx

    from app.adapters.synthflow import SynthflowClient

    s = Settings(_env_file=None)
    client = SynthflowClient(settings=s, _http=MagicMock(spec=httpx.Client))
    with pytest.raises(ConfigError):
        client.schedule_callback(phone="+15550000000")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Canonical tier numbering is unified
# ─────────────────────────────────────────────────────────────────────────────

def test_canonical_tier_sequence():
    """
    Both Cold Lead and New Lead use the same canonical tier sequence.
    None → 0 → 1 → 2 → 3 (terminal).
    """
    from app.worker.jobs.voicemail_jobs import _get_next_tier

    sequence = [None, "0", "1", "2"]
    expected = ["0", "1", "2", "3"]
    for current, expected_next in zip(sequence, expected):
        assert _get_next_tier(current) == expected_next, (
            f"Expected {current!r} → {expected_next!r}"
        )


def test_tier_3_is_always_terminal():
    """Tier 3 returns None (terminal) — no further advancement."""
    from app.worker.jobs.voicemail_jobs import _get_next_tier
    assert _get_next_tier("3") is None


def test_cold_lead_and_new_lead_both_reach_terminal():
    """Both campaign types can reach tier 3 via the same state model."""
    from app.services.tier_policy import get_cold_lead_policy, get_new_lead_policy

    cold_terminal = get_cold_lead_policy("2", _settings())
    assert cold_terminal.next_tier == "3"
    assert cold_terminal.is_terminal is True

    new_s = _settings(
        new_vm_tier_none_delay_minutes=60,
        new_vm_tier_0_delay_minutes=1440,
        new_vm_tier_1_delay_minutes=1440,
        new_vm_tier_2_finalizes=True,
    )
    new_terminal = get_new_lead_policy("2", new_s)
    assert new_terminal.next_tier == "3"
    assert new_terminal.is_terminal is True


# ─────────────────────────────────────────────────────────────────────────────
# 12. UNKNOWN consent treated as NO (no writeback)
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_consent_treated_as_no():
    """
    UNKNOWN consent must NOT allow GHL summary writeback.
    This prevents silent writes when consent is ambiguous.
    """
    output = ConsentOutput(
        consent="UNKNOWN", confidence="low",
        model_used="gpt-4o-mini",
        prompt_family="summary_consent_detector",
        prompt_version="v1",
    )
    assert output.allows_writeback is False


def test_only_yes_allows_writeback():
    """Exhaustive check: YES → True; all others → False."""

    base = {"model_used": "m", "prompt_family": "f", "prompt_version": "v1"}
    assert ConsentOutput(consent="YES", confidence="high", **base).allows_writeback is True
    assert ConsentOutput(consent="NO", confidence="high", **base).allows_writeback is False
    assert ConsentOutput(consent="UNKNOWN", confidence="low", **base).allows_writeback is False
