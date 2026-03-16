"""
Unit tests for lifecycle_jobs.py — Feature 4.

Covers:
  1.  update_lead_state: no classification row → completes without touching lead_state
  2.  update_lead_state: no lead_stage in output → completes without touching lead_state
  3.  update_lead_state: no contact_id available → completes without touching lead_state
  4.  update_lead_state: creates new LeadState row when none exists
  5.  update_lead_state: updates existing LeadState row with optimistic concurrency
  6.  update_lead_state: version conflict → raises RuntimeError, fails job
  7.  update_lead_state: job already claimed → returns immediately
  8.  update_lead_state: supports 'stage' key as alias for 'lead_stage' in output_json
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.models.call_event import CallEvent
from app.models.classification import ClassificationResult
from app.models.lead_state import LeadState


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
        job_type="update_lead_state",
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


def _make_call_event(session: Session, call_id: str, contact_id: str | None,
                     status: str = "completed") -> CallEvent:
    ev = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status=status,
        dedupe_key=f"{call_id}:test_{uuid.uuid4().hex}",
        raw_payload_json={},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ev)
    session.flush()
    return ev


def _make_classification(session: Session, call_event_id: str,
                          output: dict) -> ClassificationResult:
    cr = ClassificationResult(
        id=str(uuid.uuid4()),
        call_event_id=call_event_id,
        model_used="gpt-4o-mini",
        prompt_family="lead_stage_classifier",
        prompt_version="v1",
        output_json=output,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(cr)
    session.flush()
    return cr


def _run(session: Session, payload: dict) -> ScheduledJob:
    """Create job and run update_lead_state against the given session."""
    job = _make_job(session, payload)
    with patch("app.worker.jobs.lifecycle_jobs.get_sync_session") as mock_sess:
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)
        from app.worker.jobs.lifecycle_jobs import update_lead_state
        update_lead_state(job.id)
    return session.get(ScheduledJob, job.id)


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_no_classification_skips(session):
    """No ClassificationResult row → job completes, LeadState untouched."""
    ce = _make_call_event(session, "call-lc-1", "contact-lc-1")
    job = _run(session, {"call_id": ce.call_id, "call_event_id": ce.id,
                         "contact_id": "contact-lc-1"})
    assert job.status == "completed"

    from sqlalchemy import select
    assert session.scalars(
        select(LeadState).where(LeadState.contact_id == "contact-lc-1")
    ).first() is None


def test_no_lead_stage_in_output_skips(session):
    """Classification output has no lead_stage → job completes, LeadState untouched."""
    ce = _make_call_event(session, "call-lc-2", "contact-lc-2")
    _make_classification(session, ce.id, {"irrelevant": "data"})
    job = _run(session, {"call_id": ce.call_id, "call_event_id": ce.id,
                         "contact_id": "contact-lc-2"})
    assert job.status == "completed"

    from sqlalchemy import select
    assert session.scalars(
        select(LeadState).where(LeadState.contact_id == "contact-lc-2")
    ).first() is None


def test_no_contact_id_skips(session):
    """No contact_id anywhere → job completes, nothing written."""
    ce = _make_call_event(session, "call-lc-3", None)  # no contact_id on CallEvent
    _make_classification(session, ce.id, {"lead_stage": "Hot"})
    job = _run(session, {"call_id": ce.call_id, "call_event_id": ce.id})
    assert job.status == "completed"


def test_creates_new_lead_state(session):
    """Creates a new LeadState row when none exists for contact."""
    contact_id = f"contact-new-{uuid.uuid4().hex[:6]}"
    ce = _make_call_event(session, f"call-new-{uuid.uuid4().hex[:6]}", contact_id)
    _make_classification(session, ce.id, {"lead_stage": "Hot Lead"})

    job = _run(session, {
        "call_id": ce.call_id,
        "call_event_id": ce.id,
        "contact_id": contact_id,
    })
    assert job.status == "completed"

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead is not None
    assert lead.lead_stage == "Hot Lead"
    assert lead.last_call_status == "completed"
    assert lead.version == 0


def test_updates_existing_lead_state(session):
    """Updates an existing LeadState row with new lead_stage."""
    contact_id = f"contact-upd-{uuid.uuid4().hex[:6]}"
    ce = _make_call_event(session, f"call-upd-{uuid.uuid4().hex[:6]}", contact_id)
    _make_classification(session, ce.id, {"lead_stage": "Warm"})

    # Pre-existing row
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        lead_stage="Cold",
        last_call_status="voicemail",
        version=2,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(lead)
    session.flush()

    job = _run(session, {
        "call_id": ce.call_id,
        "call_event_id": ce.id,
        "contact_id": contact_id,
    })
    assert job.status == "completed"

    session.refresh(lead)
    assert lead.lead_stage == "Warm"
    assert lead.last_call_status == "completed"
    assert lead.version == 3


def test_version_conflict_fails_job(session):
    """Simulate optimistic concurrency conflict → RuntimeError → job fails.

    We inject a mock result with rowcount=0 on the UPDATE statement to
    replicate what happens when two workers modify the same lead concurrently.
    """
    contact_id = f"contact-conf-{uuid.uuid4().hex[:6]}"
    ce = _make_call_event(session, f"call-conf-{uuid.uuid4().hex[:6]}", contact_id)
    _make_classification(session, ce.id, {"lead_stage": "Hot"})

    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        lead_stage="Old",
        version=5,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(lead)
    session.flush()

    job_record = _make_job(session, {
        "call_id": ce.call_id,
        "call_event_id": ce.id,
        "contact_id": contact_id,
    })

    # Return rowcount=0 on the UPDATE to simulate concurrent modification
    original_execute = session.execute

    def _zero_rowcount_execute(stmt, *args, **kwargs):
        stmt_text = str(stmt).upper()
        if "UPDATE" in stmt_text and "LEAD_STATE" in stmt_text:
            zero = MagicMock()
            zero.rowcount = 0
            return zero
        return original_execute(stmt, *args, **kwargs)

    with (
        patch("app.worker.jobs.lifecycle_jobs.get_sync_session") as mock_sess,
        patch.object(session, "execute", side_effect=_zero_rowcount_execute),
        pytest.raises(RuntimeError, match="version conflict"),
    ):
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)
        from app.worker.jobs.lifecycle_jobs import update_lead_state
        update_lead_state(job_record.id)

    assert session.get(ScheduledJob, job_record.id).status == "failed"


def test_already_claimed_returns_immediately(session):
    """Already-claimed job → function exits without modifying state."""
    job = _make_job(session, {"call_event_id": "x"})
    job.status = "running"
    job.claimed_by = "other-worker"
    session.flush()

    with patch("app.worker.jobs.lifecycle_jobs.get_sync_session") as mock_sess:
        mock_sess.return_value.__enter__ = lambda _: session
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)
        from app.worker.jobs.lifecycle_jobs import update_lead_state
        update_lead_state(job.id)  # must not raise


def test_stage_alias_supported(session):
    """'stage' key in output_json is treated as lead_stage."""
    contact_id = f"contact-alias-{uuid.uuid4().hex[:6]}"
    ce = _make_call_event(session, f"call-alias-{uuid.uuid4().hex[:6]}", contact_id)
    _make_classification(session, ce.id, {"stage": "Enrolled"})

    job = _run(session, {
        "call_id": ce.call_id,
        "call_event_id": ce.id,
        "contact_id": contact_id,
    })
    assert job.status == "completed"

    from sqlalchemy import select
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead is not None
    assert lead.lead_stage == "Enrolled"
