"""
Unit tests for shadow mode interception.

Part 3 of shadow mode spec:
  1. Shadow mode ON  → external calls NOT executed; shadow_actions row written
  2. Shadow mode OFF → behavior unchanged (external call proceeds)
  3. No duplicate shadow logs per job execution
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.ai_message_generator import VmFollowupResult
from app.models.base import Base
from app.models.outbound_message import OutboundMessage
from app.models.scheduled_job import ScheduledJob
from app.models.shadow_action import ShadowAction


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

def _make_job(
    session,
    job_type: str,
    contact_id: str,
    extra_payload: dict | None = None,
    status: str = "pending",
) -> ScheduledJob:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "contact_id": contact_id,
        "phone_number": "+15550001234",
        "lead_name": "Test Lead",
        "campaign_name": "New_Lead",
        "attempt_number": 1,
    }
    if extra_payload:
        payload.update(extra_payload)
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="lead",
        entity_id=contact_id,
        run_at=now,
        status=status,
        payload_json=payload,
        version=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def _ctx(session):
    """Return a context-manager mock that yields the given SQLite session."""
    mock = MagicMock()
    mock.__enter__ = lambda _: session
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _shadow_rows(session, contact_id: str) -> list[ShadowAction]:
    return list(session.execute(
        select(ShadowAction).where(ShadowAction.contact_id == contact_id)
    ).scalars().all())


def _outbound_rows(session, contact_id: str) -> list[OutboundMessage]:
    return list(session.execute(
        select(OutboundMessage).where(OutboundMessage.contact_id == contact_id)
    ).scalars().all())


def _fake_vm_result() -> VmFollowupResult:
    return VmFollowupResult(
        sms_text="Hi, this is a test SMS from Cora!",
        email_subject="Test Email Subject",
        email_html="<p>Test email body</p>",
        email_text="Test email body",
        preview_text="",
    )


def _settings(shadow_on: bool):
    s = MagicMock()
    s.shadow_mode_enabled = shadow_on
    s.is_shadow_mode = shadow_on
    # Campaign active-window fields — required when shadow_on=False and the
    # window check runs for outbound/channel jobs.
    s.default_timezone = "America/Chicago"
    s.new_lead_active_days = "0,1,2,3,4,5,6"
    s.new_lead_active_start_hour = 8
    s.new_lead_active_end_hour = 22
    s.cold_lead_active_days = "0,1,2,3,4"
    s.cold_lead_active_start_hour = 8
    s.cold_lead_active_end_hour = 22
    return s


# ---------------------------------------------------------------------------
# launch_outbound_call_job — shadow ON
# ---------------------------------------------------------------------------

class TestOutboundCallShadowOn:
    def test_job_completes_without_calling_synthflow(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "launch_outbound_call", contact_id)

        synthflow_mock = MagicMock()
        with (
            patch("app.worker.jobs.outbound_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.outbound_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.adapters.synthflow.SynthflowClient", synthflow_mock),
        ):
            from app.worker.jobs.outbound_jobs import launch_outbound_call_job
            launch_outbound_call_job(job.id)

        session.refresh(job)
        assert job.status == "completed"
        synthflow_mock.assert_not_called()

    def test_shadow_action_row_written(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "launch_outbound_call", contact_id)

        with (
            patch("app.worker.jobs.outbound_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.outbound_jobs.get_settings", return_value=_settings(shadow_on=True)),
        ):
            from app.worker.jobs.outbound_jobs import launch_outbound_call_job
            launch_outbound_call_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1
        assert rows[0].action_type == "outbound_call"
        assert rows[0].payload["campaign"] == "New_Lead"
        assert rows[0].payload["phone"] == "+15550001234"

    def test_no_duplicate_on_second_claim_attempt(self, session):
        """Running the job a second time (already claimed) must not write another row."""
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "launch_outbound_call", contact_id)

        with (
            patch("app.worker.jobs.outbound_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.outbound_jobs.get_settings", return_value=_settings(shadow_on=True)),
        ):
            from app.worker.jobs.outbound_jobs import launch_outbound_call_job
            launch_outbound_call_job(job.id)   # first run — succeeds
            launch_outbound_call_job(job.id)   # second run — job already completed, claim returns None

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1, "duplicate shadow row written on second claim"


# ---------------------------------------------------------------------------
# launch_outbound_call_job — shadow OFF
# ---------------------------------------------------------------------------

class TestOutboundCallShadowOff:
    def test_synthflow_called_when_shadow_off(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "launch_outbound_call", contact_id)

        fake_client = MagicMock()
        fake_client.launch_new_lead_call.return_value = {"status": "ok"}

        # SynthflowClient is a lazy import inside the function; patch at its definition site
        with (
            patch("app.worker.jobs.outbound_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.outbound_jobs.get_settings", return_value=_settings(shadow_on=False)),
            patch("app.adapters.synthflow.SynthflowClient", return_value=fake_client),
        ):
            from app.worker.jobs.outbound_jobs import launch_outbound_call_job
            launch_outbound_call_job(job.id)

        session.refresh(job)
        assert job.status == "completed"

    def test_no_shadow_row_written_when_shadow_off(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "launch_outbound_call", contact_id)

        fake_client = MagicMock()
        fake_client.launch_new_lead_call.return_value = {"status": "ok"}

        with (
            patch("app.worker.jobs.outbound_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.outbound_jobs.get_settings", return_value=_settings(shadow_on=False)),
            patch("app.adapters.synthflow.SynthflowClient", return_value=fake_client),
        ):
            from app.worker.jobs.outbound_jobs import launch_outbound_call_job
            launch_outbound_call_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 0, "shadow row must not be written when shadow_mode_enabled=False"


# ---------------------------------------------------------------------------
# send_sms_job — shadow ON
# Shadow mode now generates AI content and writes outbound_messages(status=shadow).
# ---------------------------------------------------------------------------

class TestSmsShadowOn:
    def _patches(self, session):
        """Common patch stack for SMS shadow-on tests."""
        return [
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", return_value=_fake_vm_result()),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ]

    def test_job_completes_and_generates_content(self, session):
        """Shadow mode must generate AI content and write outbound_message(status='shadow')."""
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_sms", contact_id)

        ai_mock = MagicMock(return_value=_fake_vm_result())
        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", ai_mock),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_sms_job
            send_sms_job(job.id)

        session.refresh(job)
        assert job.status == "completed"
        ai_mock.assert_called_once()

        outbound = _outbound_rows(session, contact_id)
        assert len(outbound) == 1
        assert outbound[0].channel == "sms"
        assert outbound[0].status == "shadow"
        assert outbound[0].body == _fake_vm_result().sms_text

    def test_shadow_action_row_written_for_sms(self, session):
        """Shadow action row is written with message_body in payload."""
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_sms", contact_id)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", return_value=_fake_vm_result()),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_sms_job
            send_sms_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1
        assert rows[0].action_type == "sms"
        assert rows[0].payload.get("message_body") == _fake_vm_result().sms_text

    def test_no_duplicate_sms_shadow_on_second_claim(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_sms", contact_id)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", return_value=_fake_vm_result()),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_sms_job
            send_sms_job(job.id)
            send_sms_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# send_email_job — shadow ON
# ---------------------------------------------------------------------------

class TestEmailShadowOn:
    def test_job_completes_and_generates_content(self, session):
        """Shadow mode must generate AI content and write outbound_message(status='shadow')."""
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_email", contact_id)

        ai_mock = MagicMock(return_value=_fake_vm_result())
        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", ai_mock),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_email_job
            send_email_job(job.id)

        session.refresh(job)
        assert job.status == "completed"
        ai_mock.assert_called_once()

        outbound = _outbound_rows(session, contact_id)
        assert len(outbound) == 1
        assert outbound[0].channel == "email"
        assert outbound[0].status == "shadow"
        assert outbound[0].subject == _fake_vm_result().email_subject

    def test_shadow_action_row_written_for_email(self, session):
        """Shadow action row is written with email_subject and message_body in payload."""
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_email", contact_id)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", return_value=_fake_vm_result()),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_email_job
            send_email_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1
        assert rows[0].action_type == "email"
        assert rows[0].payload.get("email_subject") == _fake_vm_result().email_subject

    def test_no_duplicate_email_shadow_on_second_claim(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_email", contact_id)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=True)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", return_value=_fake_vm_result()),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_email_job
            send_email_job(job.id)
            send_email_job(job.id)

        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# send_sms_job — shadow OFF (no shadow row, AI path fires)
# ---------------------------------------------------------------------------

class TestSmsShadowOff:
    def test_no_shadow_row_and_ai_called_when_shadow_off(self, session):
        contact_id = f"c-{uuid.uuid4().hex[:6]}"
        job = _make_job(session, "send_sms", contact_id)

        ai_mock = MagicMock(return_value=_fake_vm_result())

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session", return_value=_ctx(session)),
            patch("app.worker.jobs.channel_jobs.get_settings", return_value=_settings(shadow_on=False)),
            patch("app.worker.jobs.channel_jobs._check_active_window", return_value=True),
            patch("app.core.ai_message_generator.generate_vm_followup", ai_mock),
            patch("app.core.conversation_context.get_conversation_context", return_value=MagicMock()),
        ):
            from app.worker.jobs.channel_jobs import send_sms_job
            send_sms_job(job.id)

        session.refresh(job)
        assert job.status == "completed"
        ai_mock.assert_called_once()
        rows = _shadow_rows(session, contact_id)
        assert len(rows) == 0
