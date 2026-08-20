"""
Unit tests for app/worker/jobs/internal_comment_jobs.py.

Covers:
  1.  Happy path: resolves contact, writes note, creates GhlInternalCommentLogEvent row
  2.  Already claimed → returns immediately, no writes attempted
  3.  Dedupe: skips when a 'created' GhlInternalCommentLogEvent already exists
  4.  Missing call_event_id → job fails + exception created
  5.  Missing CallEvent row → job fails + exception created
  6.  No transcript → skips cleanly (complete_job, not failed)
  7.  Contact resolution failure (no match) → job fails + exception created
  8.  Phone-derived contact_id → resolves via search_contact_by_phone
  9.  Recording URL passed through to the adapter write call
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
from app.models.ghl_internal_comment_log_event import GhlInternalCommentLogEvent


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
    transcript: str | None = "bot: hi\nhuman: hello", recording_url: str | None = "https://example.com/rec.wav",
) -> CallEvent:
    ev = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status="completed",
        dedupe_key=f"{call_id}:test_{uuid.uuid4().hex}",
        transcript=transcript,
        recording_url=recording_url,
        raw_payload_json={},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ev)
    session.flush()
    return ev


def _patched(session):
    mock_sess = patch("app.worker.jobs.internal_comment_jobs.get_sync_session")
    return mock_sess


def _bind_session(mock_sess, session):
    mock_sess.return_value.__enter__ = lambda _: session
    mock_sess.return_value.__exit__ = MagicMock(return_value=False)


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_happy_path_creates_log_row_and_completes(session):
    call_event = _make_call_event(session)
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": "+15551234567"}}),
        patch("app.adapters.ghl_internal_comment.GhlInternalCommentClient.write_call_note",
              return_value={"success": True, "messageId": "msg-1"}),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    log_row = session.scalars(
        select(GhlInternalCommentLogEvent).where(GhlInternalCommentLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is not None
    assert log_row.status == "created"
    assert log_row.ghl_message_id == "msg-1"

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


def test_write_passes_transcript_and_recording_url(session):
    call_event = _make_call_event(
        session, transcript="bot: hello there\nhuman: hi", recording_url="https://example.com/real.wav",
    )
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    captured = {}

    def _capture_write(self, **kwargs):
        captured.update(kwargs)
        return {"success": True, "messageId": "msg-1"}

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact",
              return_value={"contact": {"id": "ghl-contact-1", "phone": "+15551234567"}}),
        patch("app.adapters.ghl_internal_comment.GhlInternalCommentClient.write_call_note", _capture_write),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    assert captured["contact_id"] == "ghl-contact-1"
    assert captured["transcript"] == "bot: hello there\nhuman: hi"
    assert captured["recording_url"] == "https://example.com/real.wav"


# ─────────────────────────────────────────────────────────────────────────────
# Already claimed / dedupe
# ─────────────────────────────────────────────────────────────────────────────

def test_already_claimed_returns_immediately(session):
    job = _make_job(session, "write_internal_comment_note", {"call_event_id": "x"})
    job.status = "running"
    job.claimed_by = "other-worker"
    session.flush()

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact") as mock_get_contact,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    mock_get_contact.assert_not_called()


def test_dedupe_skips_if_log_already_exists(session):
    call_event = _make_call_event(session, call_id="call-dedup-001")
    existing = GhlInternalCommentLogEvent(
        id=str(uuid.uuid4()), call_event_id=call_event.id, status="created",
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(existing)
    session.flush()

    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id,
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact") as mock_get_contact,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    mock_get_contact.assert_not_called()
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


# ─────────────────────────────────────────────────────────────────────────────
# Failure / skip paths
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_call_event_id_fails_job(session):
    job = _make_job(session, "write_internal_comment_note", {"call_id": "call-x"})

    with (
        _patched(session) as mock_sess,
        pytest.raises(ValueError, match="call_event_id"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_missing_call_event_row_fails_job(session):
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": "call-ghost", "call_event_id": str(uuid.uuid4()),
    })

    with (
        _patched(session) as mock_sess,
        pytest.raises(ValueError, match="CallEvent not found"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_no_transcript_skips_cleanly(session):
    """No transcript (e.g. voicemail/failed call) -> complete_job, not a failure."""
    call_event = _make_call_event(session, call_id="call-no-transcript", transcript=None)
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.get_contact") as mock_get_contact,
        patch("app.adapters.ghl_internal_comment.GhlInternalCommentClient.write_call_note") as mock_write,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    mock_get_contact.assert_not_called()
    mock_write.assert_not_called()
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"
    log_row = session.scalars(
        select(GhlInternalCommentLogEvent).where(GhlInternalCommentLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is None


def test_blank_transcript_skips_cleanly(session):
    """Whitespace-only transcript is treated the same as missing."""
    call_event = _make_call_event(session, call_id="call-blank-transcript", transcript="   ")
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "ghl-contact-1",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl_internal_comment.GhlInternalCommentClient.write_call_note") as mock_write,
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    mock_write.assert_not_called()
    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "completed"


def test_contact_resolution_failure_fails_job(session):
    """Phone-derived contact_id with no search match -> ValueError, job fails (not crashes)."""
    call_event = _make_call_event(session, call_id="call-no-contact", contact_id="15559990000")
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "15559990000",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.search_contact_by_phone", return_value=None),
        pytest.raises(ValueError, match="Could not resolve a GHL contact"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"


def test_contact_resolution_transient_error_surfaces_in_failure_reason(session):
    """When search_contact_by_phone raises (transient GHL blip) rather than
    cleanly returning no match, the real error must reach the exception
    record — previously it was swallowed to a warning log only, so on-call
    had no way to tell "GHL API errored" from "contact genuinely missing"
    without re-running the lookup by hand."""
    call_event = _make_call_event(session, call_id="call-transient", contact_id="15559990001")
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "15559990001",
    })

    with (
        _patched(session) as mock_sess,
        patch(
            "app.adapters.ghl.GHLClient.search_contact_by_phone",
            side_effect=RuntimeError("timeout"),
        ),
        pytest.raises(ValueError, match="resolution_error=phone search failed: timeout"),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    job_row = session.get(ScheduledJob, job.id)
    assert job_row.status == "failed"

    from app.models.exception import ExceptionRecord
    exc_row = session.scalars(
        select(ExceptionRecord).where(ExceptionRecord.entity_id == call_event.call_id)
    ).first()
    assert exc_row is not None
    assert "resolution_error=phone search failed: timeout" in exc_row.context_json["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Phone-derived contact_id (inbound-style resolution)
# ─────────────────────────────────────────────────────────────────────────────

def test_phone_derived_contact_id_resolves_via_search(session):
    call_event = _make_call_event(session, call_id="call-phone-derived", contact_id="15551234567")
    job = _make_job(session, "write_internal_comment_note", {
        "call_id": call_event.call_id, "call_event_id": call_event.id, "contact_id": "15551234567",
    })

    with (
        _patched(session) as mock_sess,
        patch("app.adapters.ghl.GHLClient.search_contact_by_phone",
              return_value={"id": "resolved-ghl-id", "phone": "+15551234567"}) as mock_search,
        patch("app.adapters.ghl_internal_comment.GhlInternalCommentClient.write_call_note",
              return_value={"success": True, "messageId": "msg-2"}),
    ):
        _bind_session(mock_sess, session)
        from app.worker.jobs.internal_comment_jobs import write_internal_comment_note
        write_internal_comment_note(job.id)

    mock_search.assert_called_once()
    log_row = session.scalars(
        select(GhlInternalCommentLogEvent).where(GhlInternalCommentLogEvent.call_event_id == call_event.id)
    ).first()
    assert log_row is not None
    assert log_row.status == "created"
