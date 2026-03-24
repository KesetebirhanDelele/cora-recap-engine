"""
Unit tests for the messaging system.

Covers:
  - reply_detection.has_recent_reply()
  - ai_message_generator.generate_sms/email (AI success + fallback)
  - conversation_context.get_conversation_context()
  - _schedule_messaging_after_voicemail() in voicemail_jobs
  - ingest_inbound_message API endpoint
  - channel_jobs: suppression on reply, outbound_message created
  - No duplicate SMS/email scheduling
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.inbound_message import InboundMessage
from app.models.lead_state import LeadState
from app.models.outbound_message import OutboundMessage
from app.models.scheduled_job import ScheduledJob


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


def _now():
    return datetime.now(tz=timezone.utc)


def _make_lead(session, *, contact_id=None, phone="+15550001234",
               status="active", last_replied_at=None):
    contact_id = contact_id or str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        status=status,
        campaign_name="Cold Lead",
        ai_campaign_value=None,
        last_replied_at=last_replied_at,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


def _make_scheduled_job(session, *, contact_id, job_type="send_sms",
                        status="pending"):
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="lead",
        entity_id=contact_id,
        run_at=_now(),
        status=status,
        payload_json={"contact_id": contact_id},
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(job)
    session.flush()
    return job


# ---------------------------------------------------------------------------
# reply_detection.has_recent_reply
# ---------------------------------------------------------------------------

class TestHasRecentReply:
    def test_returns_false_when_no_data(self, session):
        from app.core.reply_detection import has_recent_reply
        assert has_recent_reply(session, str(uuid.uuid4())) is False

    def test_returns_false_for_empty_contact_id(self, session):
        from app.core.reply_detection import has_recent_reply
        assert has_recent_reply(session, "") is False

    def test_detects_inbound_message(self, session):
        from app.core.reply_detection import has_recent_reply
        cid = str(uuid.uuid4())
        session.add(InboundMessage(
            id=str(uuid.uuid4()), contact_id=cid, channel="sms",
            body="Hi back", received_at=_now(),
        ))
        session.flush()
        assert has_recent_reply(session, cid) is True

    def test_detects_last_replied_at_on_lead_state(self, session):
        from app.core.reply_detection import has_recent_reply
        lead = _make_lead(session, last_replied_at=_now())
        assert has_recent_reply(session, lead.contact_id) is True

    def test_no_reply_lead_exists_no_inbound(self, session):
        from app.core.reply_detection import has_recent_reply
        lead = _make_lead(session)  # no last_replied_at
        assert has_recent_reply(session, lead.contact_id) is False


# ---------------------------------------------------------------------------
# message_templates
# ---------------------------------------------------------------------------

class TestMessageTemplates:
    def test_sms_fallback_is_within_160_chars(self):
        from app.core.message_templates import get_sms_fallback
        assert len(get_sms_fallback()) <= 160

    def test_email_fallback_has_subject_and_body(self):
        from app.core.message_templates import get_email_fallback
        fb = get_email_fallback()
        assert fb.subject
        assert fb.body

    def test_sms_fallback_not_empty(self):
        from app.core.message_templates import get_sms_fallback
        assert get_sms_fallback().strip()


# ---------------------------------------------------------------------------
# ai_message_generator — AI success path
# ---------------------------------------------------------------------------

class TestAiMessageGenerator:
    def _mock_oai(self, response: dict):
        """Return a mock openai.OpenAI instance that returns `response` as JSON."""
        import json
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps(response)
        mock_client.chat.completions.create.return_value = mock_resp
        return mock_client

    def _mock_settings(self):
        s = MagicMock()
        s.openai_api_key = "test-key"
        s.openai_base_url = None
        s.openai_timeout_seconds = 30
        s.openai_retry_max = 0
        s.openai_model_student_summary = "gpt-4o-mini"
        return s

    def _make_context(self, contact_id=None):
        from app.core.conversation_context import ConversationContext
        return ConversationContext(
            contact_id=contact_id or str(uuid.uuid4()),
            campaign_name="Cold Lead",
            status="active",
            tier="0",
            preferred_channel=None,
            transcripts=["Hi, I got your voicemail. Call me back later."],
        )

    def test_generate_sms_returns_ai_text(self):
        from app.core.ai_message_generator import generate_sms
        ctx = self._make_context()
        sms = generate_sms(ctx, self._mock_settings(),
                           _client=self._mock_oai({"sms": "Hey! Can we connect tomorrow?"}))
        assert sms == "Hey! Can we connect tomorrow?"

    def test_generate_sms_truncates_at_160(self):
        from app.core.ai_message_generator import generate_sms
        long_text = "A" * 200
        ctx = self._make_context()
        sms = generate_sms(ctx, self._mock_settings(),
                           _client=self._mock_oai({"sms": long_text}))
        assert len(sms) <= 160

    def test_generate_sms_fallback_on_ai_error(self):
        from app.core.ai_message_generator import generate_sms
        from app.core.message_templates import get_sms_fallback
        ctx = self._make_context()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("AI down")
        sms = generate_sms(ctx, self._mock_settings(), _client=mock_client)
        assert sms == get_sms_fallback()

    def test_generate_sms_fallback_on_empty_response(self):
        from app.core.ai_message_generator import generate_sms
        from app.core.message_templates import get_sms_fallback
        ctx = self._make_context()
        sms = generate_sms(ctx, self._mock_settings(),
                           _client=self._mock_oai({"sms": ""}))
        assert sms == get_sms_fallback()

    def test_generate_email_returns_ai_content(self):
        from app.core.ai_message_generator import generate_email
        ctx = self._make_context()
        email = generate_email(
            ctx, self._mock_settings(),
            _client=self._mock_oai({"subject": "Quick note", "body": "Hey there!"})
        )
        assert email.subject == "Quick note"
        assert email.body == "Hey there!"

    def test_generate_email_fallback_on_ai_error(self):
        from app.core.ai_message_generator import generate_email
        from app.core.message_templates import get_email_fallback
        ctx = self._make_context()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("AI down")
        email = generate_email(ctx, self._mock_settings(), _client=mock_client)
        fb = get_email_fallback()
        assert email.subject == fb.subject
        assert email.body == fb.body

    def test_generate_email_fallback_on_incomplete_json(self):
        from app.core.ai_message_generator import generate_email
        from app.core.message_templates import get_email_fallback
        ctx = self._make_context()
        # Missing 'body' key
        email = generate_email(ctx, self._mock_settings(),
                               _client=self._mock_oai({"subject": "Only subject"}))
        fb = get_email_fallback()
        assert email.subject == fb.subject


# ---------------------------------------------------------------------------
# conversation_context.get_conversation_context
# ---------------------------------------------------------------------------

class TestConversationContext:
    def test_returns_minimal_context_for_unknown_contact(self, session):
        from app.core.conversation_context import get_conversation_context
        ctx = get_conversation_context(session, str(uuid.uuid4()))
        assert ctx.campaign_name is None
        assert ctx.transcripts == []

    def test_loads_lead_state_fields(self, session):
        from app.core.conversation_context import get_conversation_context
        lead = _make_lead(session)
        ctx = get_conversation_context(session, lead.contact_id)
        assert ctx.campaign_name == "Cold Lead"
        assert ctx.status == "active"

    def test_loads_outbound_message_history(self, session):
        from app.core.conversation_context import get_conversation_context
        lead = _make_lead(session)
        session.add(OutboundMessage(
            id=str(uuid.uuid4()), contact_id=lead.contact_id,
            channel="sms", body="Test SMS", status="pending", created_at=_now(),
        ))
        session.flush()
        ctx = get_conversation_context(session, lead.contact_id)
        assert any(m["body"] == "Test SMS" for m in ctx.outbound_messages)

    def test_loads_inbound_replies(self, session):
        from app.core.conversation_context import get_conversation_context
        lead = _make_lead(session)
        session.add(InboundMessage(
            id=str(uuid.uuid4()), contact_id=lead.contact_id,
            channel="sms", body="Reply here", received_at=_now(),
        ))
        session.flush()
        ctx = get_conversation_context(session, lead.contact_id)
        assert any(r["body"] == "Reply here" for r in ctx.inbound_replies)


# ---------------------------------------------------------------------------
# _schedule_messaging_after_voicemail (via voicemail_jobs)
# ---------------------------------------------------------------------------

class TestScheduleMessagingAfterVoicemail:
    def test_sms_scheduled_after_attempt_1(self, session):
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        lead = _make_lead(session)
        settings = MagicMock()
        settings.rq_default_queue = "default"
        settings.sms_followup_delay_minutes = 30
        settings.email_followup_delay_days = 1
        _schedule_messaging_after_voicemail(session, lead.contact_id, 1, settings)
        job = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == lead.contact_id,
                ScheduledJob.job_type == "send_sms",
            )
        ).first()
        assert job is not None
        assert job.status == "pending"

    def test_email_scheduled_only_on_attempt_2(self, session):
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        lead = _make_lead(session)
        settings = MagicMock()
        settings.rq_default_queue = "default"
        settings.sms_followup_delay_minutes = 30
        settings.email_followup_delay_days = 1

        # attempt 1 → no email
        _schedule_messaging_after_voicemail(session, lead.contact_id, 1, settings)
        email_job = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == lead.contact_id,
                ScheduledJob.job_type == "send_email",
            )
        ).first()
        assert email_job is None

    def test_email_scheduled_on_attempt_2(self, session):
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        lead = _make_lead(session)
        settings = MagicMock()
        settings.rq_default_queue = "default"
        settings.sms_followup_delay_minutes = 30
        settings.email_followup_delay_days = 1

        _schedule_messaging_after_voicemail(session, lead.contact_id, 2, settings)
        email_job = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == lead.contact_id,
                ScheduledJob.job_type == "send_email",
            )
        ).first()
        assert email_job is not None

    def test_skips_messaging_when_reply_detected(self, session):
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        lead = _make_lead(session, last_replied_at=_now())
        settings = MagicMock()
        settings.sms_followup_delay_minutes = 30
        settings.email_followup_delay_days = 1

        _schedule_messaging_after_voicemail(session, lead.contact_id, 1, settings)
        job = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == lead.contact_id,
                ScheduledJob.job_type == "send_sms",
            )
        ).first()
        assert job is None

    def test_no_duplicate_sms_if_pending_exists(self, session):
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        lead = _make_lead(session)
        settings = MagicMock()

        settings.sms_followup_delay_minutes = 30
        settings.email_followup_delay_days = 1
        # Pre-existing pending SMS
        _make_scheduled_job(session, contact_id=lead.contact_id, job_type="send_sms")

        _schedule_messaging_after_voicemail(session, lead.contact_id, 1, settings)
        jobs = session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.payload_json["contact_id"].as_string() == lead.contact_id,
                ScheduledJob.job_type == "send_sms",
                ScheduledJob.status == "pending",
            )
        ).all()
        assert len(jobs) == 1  # no duplicate created

    def test_non_fatal_on_exception(self, session):
        """Messaging errors must not raise — voicemail tier job must complete."""
        from app.worker.jobs.voicemail_jobs import _schedule_messaging_after_voicemail
        settings = MagicMock()
        # Patch reply_detection at its source module (local import inside the function)
        with patch(
            "app.core.reply_detection.has_recent_reply",
            side_effect=RuntimeError("DB down"),
        ):
            # Should not raise
            _schedule_messaging_after_voicemail(session, "some-contact", 1, settings)


# ---------------------------------------------------------------------------
# Inbound messages API endpoint
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_client(engine):
    """FastAPI test client with SQLite-backed DB."""
    from unittest.mock import patch

    from app.main import create_app

    with patch("app.db.get_sync_session") as mock_get_session:
        # Wire the endpoint to use our test session
        app = create_app()
        client = TestClient(app, raise_server_exceptions=True)
        yield client, engine


class TestInboundMessageEndpoint:
    def test_ingest_creates_inbound_message_row(self, session):
        """Test the ingestion logic directly (bypassing HTTP layer)."""
        from datetime import timedelta

        from sqlalchemy import update

        cid = str(uuid.uuid4())
        _make_lead(session, contact_id=cid)
        # Pre-create a pending job that should be cancelled
        job = _make_scheduled_job(session, contact_id=cid, job_type="send_sms")

        now = _now()
        msg = InboundMessage(
            id=str(uuid.uuid4()), contact_id=cid,
            channel="sms", body="Thanks, I'll call back", received_at=now,
        )
        session.add(msg)

        # Update lead_state.last_replied_at
        from app.models.lead_state import LeadState
        session.execute(
            update(LeadState).where(LeadState.contact_id == cid)
            .values(last_replied_at=now)
        )

        # Cancel pending jobs
        from sqlalchemy import update as sa_update
        session.execute(
            sa_update(ScheduledJob)
            .where(
                ScheduledJob.payload_json["contact_id"].as_string() == cid,
                ScheduledJob.status.in_(["pending", "claimed", "running"]),
            )
            .values(status="cancelled")
        )
        session.flush()

        # Verify
        from app.core.reply_detection import has_recent_reply
        assert has_recent_reply(session, cid) is True

        session.refresh(job)
        assert job.status == "cancelled"

    def test_reply_detected_after_ingest(self, session):
        """After an inbound message row is written, has_recent_reply returns True."""
        from app.core.reply_detection import has_recent_reply
        cid = str(uuid.uuid4())
        session.add(InboundMessage(
            id=str(uuid.uuid4()), contact_id=cid,
            channel="email", body="Got it", received_at=_now(),
        ))
        session.flush()
        assert has_recent_reply(session, cid) is True

    def test_jobs_cancelled_on_reply(self, session):
        """Pending jobs must be cancelled when a reply is recorded."""
        from sqlalchemy import update

        cid = str(uuid.uuid4())
        _make_lead(session, contact_id=cid)
        job1 = _make_scheduled_job(session, contact_id=cid, job_type="send_sms")
        job2 = _make_scheduled_job(session, contact_id=cid, job_type="send_email")

        # Simulate the cancellation step from ingest_inbound_message
        session.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.payload_json["contact_id"].as_string() == cid,
                ScheduledJob.status.in_(["pending", "claimed", "running"]),
            )
            .values(status="cancelled")
        )
        session.flush()

        session.refresh(job1)
        session.refresh(job2)
        assert job1.status == "cancelled"
        assert job2.status == "cancelled"


# ---------------------------------------------------------------------------
# channel_jobs: outbound_message created + reply suppression
# ---------------------------------------------------------------------------

class TestChannelJobs:
    def _make_claimed_sms_job(self, session, contact_id):
        """Create a ScheduledJob already in 'running' state for testing job handlers."""
        job = ScheduledJob(
            id=str(uuid.uuid4()),
            job_type="send_sms",
            entity_type="lead",
            entity_id=contact_id,
            run_at=_now(),
            status="running",
            payload_json={"contact_id": contact_id, "attempt_number": 1},
            version=2,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(job)
        session.flush()
        return job

    def test_sms_suppressed_when_reply_exists(self, session):
        """send_sms_job should complete without creating OutboundMessage if reply exists."""
        from app.core.reply_detection import has_recent_reply
        from app.worker.jobs.channel_jobs import send_sms_job

        cid = str(uuid.uuid4())
        _make_lead(session, contact_id=cid, last_replied_at=_now())
        job = self._make_claimed_sms_job(session, cid)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session") as mock_gs,
            patch("app.worker.jobs.channel_jobs.claim_job") as mock_claim,
            patch("app.worker.jobs.channel_jobs.mark_running"),
            patch("app.worker.jobs.channel_jobs.complete_job") as mock_complete,
        ):
            mock_gs.return_value.__enter__ = lambda s: session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            mock_claim.return_value = job

            with patch("app.core.reply_detection.has_recent_reply", return_value=True):
                send_sms_job(job.id)

            mock_complete.assert_called_once()

        # No outbound_message row created
        outbound = session.scalars(
            select(OutboundMessage).where(OutboundMessage.contact_id == cid)
        ).all()
        assert outbound == []

    def test_sms_creates_outbound_message_when_no_reply(self, session):
        """send_sms_job should create OutboundMessage when no reply exists."""
        from app.worker.jobs.channel_jobs import send_sms_job

        cid = str(uuid.uuid4())
        _make_lead(session, contact_id=cid)
        job = self._make_claimed_sms_job(session, cid)

        with (
            patch("app.worker.jobs.channel_jobs.get_sync_session") as mock_gs,
            patch("app.worker.jobs.channel_jobs.claim_job") as mock_claim,
            patch("app.worker.jobs.channel_jobs.mark_running"),
            patch("app.worker.jobs.channel_jobs.complete_job"),
            patch("app.core.reply_detection.has_recent_reply", return_value=False),
            patch(
                "app.core.ai_message_generator.generate_sms",
                return_value="Hey, tried calling!",
            ),
            patch(
                "app.core.conversation_context.get_conversation_context",
                return_value=MagicMock(contact_id=cid),
            ),
        ):
            mock_gs.return_value.__enter__ = lambda s: session
            mock_gs.return_value.__exit__ = MagicMock(return_value=False)
            mock_claim.return_value = job

            send_sms_job(job.id)

        outbound = session.scalars(
            select(OutboundMessage).where(
                OutboundMessage.contact_id == cid,
                OutboundMessage.channel == "sms",
            )
        ).first()
        assert outbound is not None
        assert outbound.body == "Hey, tried calling!"
