"""
Unit tests for app/worker/jobs/conversation_log_jobs.py — spec/19, spec/20.

Covers:
  1.  Happy path: resolves contact, writes call log, creates GhlConversationLogEvent row
  2.  Already claimed → returns immediately, no writes attempted
  3.  Dedupe: skips when a 'created' GhlConversationLogEvent already exists
  4.  Missing call_event_id → job fails + exception created
  5.  Missing CallEvent row → job fails + exception created
  6.  No OAuth token for location → skips cleanly (complete_job, not failed)
  7.  Contact resolution failure (no phone found) → job fails + exception created
  8.  Phone-derived contact_id → resolves via search_contact_by_phone
  9.  Uses call_event.agent_phone_number as call.from
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.models.ghl_conversation_log_event import GhlConversationLogEvent


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


def _make_job(session: Session, job_type: str, payload: dict) -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type=job_type,
        entity_type="call",
        entity_id=payload.get("call_id") or payload.get("call_event_id", "test"),
        status="pending",
        run_at=datetime.now(tz=timezone.utc),
        payload_json=payload,
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()
    return job


def _make_call_event(
    session: Session, call_id: str = "call-001", contact_id: str | None = "contact-001",
    duration_seconds: int = 104, agent_phone_number: str = "+14155552672",
) -> CallEvent:
    ev = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status="completed",
        dedupe_key=f"{call_id}:test_{uuid.uuid4().hex}",
        duration_seconds=duration_seconds,
        agent_phone_number=agent_phone_number,
        raw_payload_json={},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ev)
    session.flush()
    return ev


def _patched(session):
    """Common patch set: get_sync_session bound to the test session."""
    mock_sess = patch("app.worker.jobs.conversation_log_jobs.get_sync_session")
    return mock_sess


def _bind_session(mock_sess, session):
    mock_sess.return_value.__enter__ = lambda _: session
    mock_sess.return_value.__exit__ = MagicMock(return_value=False)


_SHADOW_RESULT = {"shadow": True, "operation": "write_outbound_call", "contact_id": "ghl-contact-1", "payload": {}}


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_happy_path_creates_log_row_and_completes(session):
    call_event = _make_call_event(session)
    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": "+15551234567"}}),
        patch("app.services.ghl_oauth.get_valid_access_token", return_value="location-token"),
        patch("app.adapters.ghl_conversations.GhlConversationsClient.write_outbound_call",
              return_value={"success": True, "messageId": "msg-1"}),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    log_row = session.scalars(
        select(GhlConversationLogEvent).where(GhlConversationLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is not None
    assert log_row.status == "created"
    assert log_row.ghl_message_id == "msg-1"

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


def test_write_uses_resolved_contact_phone_and_agent_phone(session):
    """call.to = the contact's phone on file, call.from = CallEvent.agent_phone_number."""
    call_event = _make_call_event(session, agent_phone_number="+14155559999")
    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    captured = {}

    def _capture_write(self, access_token, **kwargs):
        captured.update(kwargs)
        return {"success": True, "messageId": "msg-1"}

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": "+15551234567"}}),
        patch("app.services.ghl_oauth.get_valid_access_token", return_value="location-token"),
        patch("app.adapters.ghl_conversations.GhlConversationsClient.write_outbound_call", _capture_write),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    assert captured["to_phone"] == "+15551234567"
    assert captured["from_phone"] == "+14155559999"
    assert captured["call_duration_seconds"] == 104


# ─────────────────────────────────────────────────────────────────────────────
# Already claimed / dedupe
# ─────────────────────────────────────────────────────────────────────────────

def test_already_claimed_returns_immediately(session):
    job = _make_job(session, "write_conversation_log", {"call_event_id": "x"})
    job.status = "running"
    job.claimed_by = "other-worker"
    session.flush()

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact") as mock_get_contact,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    mock_get_contact.assert_not_called()


def test_dedupe_skips_if_log_already_exists(session):
    call_event = _make_call_event(session, call_id="call-dedup-001")
    existing = GhlConversationLogEvent(
        id=str(uuid.uuid4()), call_event_id=call_event.id, status="created",
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(existing)
    session.flush()

    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id,
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact") as mock_get_contact,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    mock_get_contact.assert_not_called()
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


# ─────────────────────────────────────────────────────────────────────────────
# Failure paths
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_call_event_id_fails_job(session):
    job = _make_job(session, "write_conversation_log", {"call_id": "call-x"})

    with (
        _patched(session) as mock_sess,
        pytest.raises(ValueError, match="call_event_id"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_missing_call_event_row_fails_job(session):
    job = _make_job(session, "write_conversation_log", {
        "call_id": "call-ghost", "call_event_id": str(uuid.uuid4()),
    })

    with (
        _patched(session) as mock_sess,
        pytest.raises(ValueError, match="CallEvent not found"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_contact_resolution_failure_fails_job(session):
    """Contact found but no phone on file -> ValueError, non-fatal to caller (job fails, not crashes)."""
    call_event = _make_call_event(session, call_id="call-no-phone")
    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": None}}),
        pytest.raises(ValueError, match="Could not resolve a GHL contact"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_no_oauth_token_skips_cleanly(session):
    """OAuth app not installed for this location -> complete_job, not a failure."""
    call_event = _make_call_event(session, call_id="call-no-token")
    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": "+15551234567"}}),
        patch("app.services.ghl_oauth.get_valid_access_token", return_value=None),
        patch("app.adapters.ghl_conversations.GhlConversationsClient.write_outbound_call") as mock_write,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    mock_write.assert_not_called()
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"
    log_row = session.scalars(
        select(GhlConversationLogEvent).where(GhlConversationLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is None  # no attempt was logged — nothing was written


# ─────────────────────────────────────────────────────────────────────────────
# Phone-derived contact_id (inbound-style resolution)
# ─────────────────────────────────────────────────────────────────────────────

def test_phone_derived_contact_id_resolves_via_search(session):
    call_event = _make_call_event(session, call_id="call-phone-derived", contact_id="15551234567")
    job = _make_job(session, "write_conversation_log", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "15551234567",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.search_contact_by_phone",
              return_value={"id": "resolved-ghl-id", "phone": "+15551234567"}) as mock_search,
        patch("app.services.ghl_oauth.get_valid_access_token", return_value="location-token"),
        patch("app.adapters.ghl_conversations.GhlConversationsClient.write_outbound_call",
              return_value={"success": True, "messageId": "msg-2"}),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.conversation_log_jobs import write_conversation_log
        write_conversation_log(job.id)

    mock_search.assert_called_once()
    log_row = session.scalars(
        select(GhlConversationLogEvent).where(GhlConversationLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is not None
    assert log_row.status == "created"
