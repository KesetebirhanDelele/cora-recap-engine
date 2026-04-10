"""
Unit tests for inbound call processing parity fixes.

Covers:
  Chunk 1 — _create_call_event stores campaign_name from payload
  Chunk 2 — _route_to_call_through forwards campaign_name in job payload
  Chunk 3 — _resolve_lead_state finds lead via normalized_phone fallback
  Chunk 4 — run_call_analysis creates LeadState stub for new inbound callers
  Chunk 5 — update_lead_state sets campaign_name when creating new LeadState row
  Chunk 6 — create_crm_task uses phone-search path when contact_id is a phone string
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob
from app.worker.jobs.call_processing import _create_call_event
from app.worker.jobs.ai_jobs import _resolve_lead_state


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


def _call_id() -> str:
    return str(uuid.uuid4())


def _make_call_event(session: Session, contact_id: str, phone: str | None = None) -> CallEvent:
    ce = CallEvent(
        id=str(uuid.uuid4()),
        call_id=_call_id(),
        contact_id=contact_id,
        status="completed",
        direction="inbound",
        voice_agent="Inbound",
        raw_payload_json={
            "phone_number_from": phone or contact_id,
            "campaign_name": "Inbound",
        },
        dedupe_key=str(uuid.uuid4()),
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(ce)
    session.flush()
    return ce


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 1 — _create_call_event stores campaign_name
# ─────────────────────────────────────────────────────────────────────────────

def test_create_call_event_sets_campaign_name_inbound(session):
    """Inbound payload: campaign_name='Inbound' is stored on CallEvent."""
    payload = {
        "call_id": _call_id(),
        "contact_id": "+12145551001",
        "direction": "inbound",
        "Agent": "Cora Inbound - Completed Call",
        "campaign_name": "Inbound",
        "call_status": "completed",
    }
    event = _create_call_event(session, payload["call_id"], payload, "completed")
    assert event.campaign_name == "Inbound"


def test_create_call_event_sets_campaign_name_new_lead(session):
    """New Lead outbound payload: campaign_name='New Lead' is stored on CallEvent."""
    payload = {
        "call_id": _call_id(),
        "contact_id": "ghl-contact-abc",
        "direction": "outbound",
        "Agent": "Cora Outbound NewLead Completed Call",
        "campaign_name": "New Lead",
        "call_status": "completed",
    }
    event = _create_call_event(session, payload["call_id"], payload, "completed")
    assert event.campaign_name == "New Lead"


def test_create_call_event_campaign_name_none_when_absent(session):
    """No campaign_name in payload → column is None (not defaulted)."""
    payload = {
        "call_id": _call_id(),
        "contact_id": "ghl-contact-xyz",
        "direction": "outbound",
        "call_status": "completed",
    }
    event = _create_call_event(session, payload["call_id"], payload, "completed")
    assert event.campaign_name is None


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 2 — _route_to_call_through forwards campaign_name
# ─────────────────────────────────────────────────────────────────────────────

def test_route_to_call_through_forwards_campaign_name(session):
    """campaign_name is included in the scheduled run_call_analysis job payload."""
    from app.worker.jobs.call_processing import _route_to_call_through

    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_call_event",
        entity_type="call",
        entity_id="call-123",
        status="running",
        run_at=datetime.now(tz=timezone.utc),
        payload_json={},
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()

    settings = MagicMock()
    settings.redis_url = None
    settings.redis_host = "localhost"
    settings.redis_port = 6379
    settings.redis_db = 0
    settings.redis_username = None
    settings.redis_password = None
    settings.rq_ai_queue = "ai"

    with patch("app.worker.jobs.call_processing._make_ai_queue", return_value=None):
        _route_to_call_through(
            session, job, "call-123", "contact-456", "event-789", settings,
            campaign_name="Inbound",
        )

    scheduled = session.query(ScheduledJob).filter_by(job_type="run_call_analysis").first()
    assert scheduled is not None
    assert scheduled.payload_json.get("campaign_name") == "Inbound"


def test_route_to_call_through_campaign_name_none_by_default(session):
    """campaign_name defaults to None when not provided (backwards compat)."""
    from app.worker.jobs.call_processing import _route_to_call_through

    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_call_event",
        entity_type="call",
        entity_id="call-999",
        status="running",
        run_at=datetime.now(tz=timezone.utc),
        payload_json={},
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()

    settings = MagicMock()
    settings.redis_url = None
    settings.redis_host = "localhost"
    settings.redis_port = 6379
    settings.redis_db = 0
    settings.redis_username = None
    settings.redis_password = None
    settings.rq_ai_queue = "ai"

    with patch("app.worker.jobs.call_processing._make_ai_queue", return_value=None):
        _route_to_call_through(
            session, job, "call-999", "contact-999", "event-999", settings,
        )

    scheduled = session.query(ScheduledJob).filter_by(
        entity_id="call-999", job_type="run_call_analysis"
    ).first()
    assert scheduled is not None
    assert "campaign_name" in scheduled.payload_json
    assert scheduled.payload_json["campaign_name"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 3 — _resolve_lead_state phone fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_lead_state_direct_contact_id(session):
    """Direct contact_id match is returned without touching normalized_phone."""
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id="ghl-direct-001",
        normalized_phone="+15550000001",
        campaign_name="New Lead",
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(lead)
    session.flush()

    call_event = MagicMock()
    call_event.raw_payload_json = {}

    result = _resolve_lead_state(session, "ghl-direct-001", call_event)
    assert result is not None
    assert result.contact_id == "ghl-direct-001"


def test_resolve_lead_state_phone_fallback(session):
    """When contact_id is a phone string that doesn't match, falls back to normalized_phone."""
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id="ghl-outbound-002",
        normalized_phone="+15550000002",
        campaign_name="New Lead",
        version=0,
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(lead)
    session.flush()

    call_event = MagicMock()
    call_event.raw_payload_json = {"phone_number_from": "+15550000002"}

    # contact_id is the phone (inbound derived) — won't match ghl-outbound-002
    result = _resolve_lead_state(session, "+15550000002", call_event)
    assert result is not None
    assert result.contact_id == "ghl-outbound-002"
    assert result.campaign_name == "New Lead"


def test_resolve_lead_state_returns_none_for_unknown(session):
    """Returns None when neither contact_id nor phone matches any LeadState row."""
    call_event = MagicMock()
    call_event.raw_payload_json = {"phone_number_from": "+19990000099"}

    result = _resolve_lead_state(session, "+19990000099", call_event)
    assert result is None


def test_resolve_lead_state_none_contact_id(session):
    """Returns None when contact_id is None without raising."""
    result = _resolve_lead_state(session, None, MagicMock())
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 4 — Stub LeadState creation for new inbound callers
# ─────────────────────────────────────────────────────────────────────────────

def test_inbound_new_caller_creates_lead_state_stub(session):
    """
    A completed inbound call from a brand-new caller creates a LeadState stub
    with campaign_name='Inbound', status='active', and normalized_phone set.
    """
    phone = "+15559990001"
    call_id = _call_id()
    contact_id = phone  # derived from phone in webhook normalizer

    # Verify no pre-existing LeadState
    existing = session.query(LeadState).filter_by(contact_id=contact_id).first()
    assert existing is None

    # Build a minimal job + call event matching what the worker would see
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="run_call_analysis",
        entity_type="call",
        entity_id=call_id,
        status="claimed",
        run_at=datetime.now(tz=timezone.utc),
        payload_json={
            "call_id": call_id,
            "contact_id": contact_id,
            "call_event_id": None,
            "campaign_name": "Inbound",
        },
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)

    call_event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        status="completed",
        direction="inbound",
        voice_agent="Inbound",
        transcript="Hi, I'm interested in enrolling.",
        raw_payload_json={
            "phone_number_from": phone,
            "campaign_name": "Inbound",
            "Agent": "Cora Inbound - Completed Call",
        },
        dedupe_key=f"{call_id}:process_call_event",
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(call_event)
    session.flush()

    # Update job payload to reference the real call_event_id
    job.payload_json = {**job.payload_json, "call_event_id": call_event.id}
    session.flush()

    with (
        patch("app.worker.jobs.ai_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.ai_jobs.mark_running"),
        patch("app.worker.jobs.ai_jobs.complete_job"),
        patch("app.worker.jobs.ai_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.ai_jobs.get_settings", return_value=MagicMock(
            task_create_on_completed_call=False,
            enable_student_summary_writeback=False,
        )),
        patch("app.adapters.openai_client.OpenAIClient"),
        patch("app.services.ai.generate_call_analysis", return_value=MagicMock(
            model_used="gpt-4o-mini", prompt_family="test", prompt_version="v1", raw={},
        )),
        patch("app.services.ai.generate_student_summary", return_value=MagicMock(
            student_summary="summary", summary_offered=True,
            model_used="gpt-4o-mini", prompt_family="test", prompt_version="v1",
        )),
        patch("app.services.ai.detect_consent", return_value=MagicMock(consent="NO")),
        patch("app.core.intent_detection.detect_intent", return_value={
            "intent": "interested_not_now",
            "confidence": 0.9,
            "entities": {},
        }),
        patch("app.core.intent_actions.handle_intent"),
        patch("app.core.campaigns.evaluate_campaign_switch", return_value=None),
        patch("app.worker.jobs.ai_jobs._schedule_update_lead_state"),
        patch("app.worker.jobs.ai_jobs.get_sync_session") as mock_ctx,
    ):
        mock_ctx.return_value.__enter__ = lambda _: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.ai_jobs import run_call_analysis
        run_call_analysis(job.id)

    stub = session.query(LeadState).filter_by(contact_id=contact_id).first()
    assert stub is not None, "LeadState stub should have been created for new inbound caller"
    assert stub.campaign_name == "Inbound"
    assert stub.status == "active"
    assert stub.normalized_phone == phone


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 5 — update_lead_state sets campaign_name on new row
# ─────────────────────────────────────────────────────────────────────────────

def test_update_lead_state_new_row_sets_campaign_name(session):
    """
    When update_lead_state creates a new LeadState row (no prior row exists),
    campaign_name is populated from call_event.raw_payload_json['campaign_name'].
    """
    phone = "+15559990002"
    call_id = _call_id()
    contact_id = phone

    call_event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        status="completed",
        direction="inbound",
        voice_agent="Inbound",
        raw_payload_json={
            "phone_number_from": phone,
            "campaign_name": "Inbound",
        },
        dedupe_key=str(uuid.uuid4()),
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(call_event)

    from app.models.classification import ClassificationResult
    classification = ClassificationResult(
        id=str(uuid.uuid4()),
        call_event_id=call_event.id,
        model_used="gpt-4o-mini",
        prompt_family="lead_stage_classifier",
        prompt_version="v1",
        output_json={"lead_stage": "interested"},
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(classification)

    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="update_lead_state",
        entity_type="call",
        entity_id=call_id,
        status="claimed",
        run_at=datetime.now(tz=timezone.utc),
        payload_json={
            "call_id": call_id,
            "call_event_id": call_event.id,
            "contact_id": contact_id,
        },
        created_at=datetime.now(tz=timezone.utc),
        version=0,
    )
    session.add(job)
    session.flush()

    with (
        patch("app.worker.jobs.lifecycle_jobs.claim_job", return_value=job),
        patch("app.worker.jobs.lifecycle_jobs.mark_running"),
        patch("app.worker.jobs.lifecycle_jobs.complete_job"),
        patch("app.worker.jobs.lifecycle_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.lifecycle_jobs.get_settings", return_value=MagicMock()),
        patch("app.worker.jobs.lifecycle_jobs.get_sync_session") as mock_ctx,
    ):
        mock_ctx.return_value.__enter__ = lambda _: session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from app.worker.jobs.lifecycle_jobs import update_lead_state
        update_lead_state(job.id)

    lead = session.query(LeadState).filter_by(contact_id=contact_id).first()
    assert lead is not None
    assert lead.campaign_name == "Inbound"
    assert lead.lead_stage == "interested"


# ─────────────────────────────────────────────────────────────────────────────
# Chunk 6 — create_crm_task uses phone-search when contact_id is a phone string
# ─────────────────────────────────────────────────────────────────────────────

def test_create_crm_task_phone_contact_id_uses_search():
    """
    When effective_contact_id looks like a phone number, create_crm_task must
    call search_contact_by_phone instead of get_contact, and use the resolved
    GHL ID for all subsequent GHL operations.
    """
    from app.worker.jobs.crm_jobs import create_crm_task

    phone = "+12145550001"
    resolved_ghl_id = "ghl-resolved-contact-abc"
    call_event_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    mock_session = MagicMock()
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.payload_json = {
        "call_id": call_id,
        "call_event_id": call_event_id,
        "contact_id": phone,  # phone-derived contact_id
    }

    mock_call_event = MagicMock()
    mock_call_event.contact_id = phone
    mock_call_event.transcript = "I am interested in the program."
    mock_call_event.duration_seconds = 90
    mock_call_event.raw_payload_json = {
        "phone_number_from": phone,
        "campaign_name": "Inbound",
    }

    mock_lead = MagicMock()
    mock_lead.normalized_phone = phone

    mock_session.get.return_value = mock_call_event
    mock_session.scalars.return_value.first.return_value = mock_lead

    mock_ghl = MagicMock()
    mock_ghl.search_contact_by_phone.return_value = {"id": resolved_ghl_id, "contact": {}}
    mock_ghl.update_contact_fields.return_value = {"shadow": True}
    mock_ghl.create_task.return_value = {"shadow": True}

    mock_analysis = MagicMock()
    mock_analysis.lead_classification = "Interested"
    mock_analysis.is_lead_classification = True
    mock_analysis.assign_to = "advisor@example.com"
    mock_analysis.task_description = "Follow up"
    mock_analysis.task_title = "Call follow-up"
    mock_analysis.task_due_date = "2026-04-15"
    mock_analysis.ai_campaign = "Yes"
    mock_analysis.create_task = False
    mock_analysis.call_detailed_summary = "Lead is interested."
    mock_analysis.call_start_time_formatted = "2026-04-10 10:00"

    settings = MagicMock()
    settings.ghl_field_mark_as_lead = "mark_as_lead"
    settings.ghl_field_ai_lead_assign_to = "assign_to"
    settings.ghl_field_support_ticket_3 = "ticket_3"
    settings.ghl_field_ai_lead_classification = "classification"
    settings.ghl_field_ai_campaign = "ai_campaign"

    with (
        patch("app.worker.jobs.crm_jobs.claim_job", return_value=mock_job),
        patch("app.worker.jobs.crm_jobs.mark_running"),
        patch("app.worker.jobs.crm_jobs.complete_job"),
        patch("app.worker.jobs.crm_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.crm_jobs.get_settings", return_value=settings),
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_ctx,
        patch("app.adapters.ghl.GHLClient", return_value=mock_ghl),
        patch("app.core.ai_message_generator.generate_ghl_call_analysis", return_value=mock_analysis),
        patch("app.worker.jobs.crm_jobs._persist_ghl_analysis"),
    ):
        mock_ctx.return_value.__enter__ = lambda _: mock_session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.first.side_effect = [mock_lead, None]

        create_crm_task(job_id)

    # get_contact must NOT have been called with a phone string
    mock_ghl.get_contact.assert_not_called()
    # search_contact_by_phone must have been called with the phone
    mock_ghl.search_contact_by_phone.assert_called_once_with(phone)
    # update_contact_fields must use the resolved GHL ID, not the phone string
    call_args = mock_ghl.update_contact_fields.call_args
    assert call_args.kwargs.get("contact_id") == resolved_ghl_id or \
           (call_args.args and call_args.args[0] == resolved_ghl_id), \
           f"Expected GHL ID {resolved_ghl_id!r} but got {call_args}"


def test_create_crm_task_real_ghl_id_uses_get_contact():
    """
    When contact_id is a real GHL ID (non-phone string), get_contact is called
    and search_contact_by_phone is not.
    """
    from app.worker.jobs.crm_jobs import create_crm_task

    ghl_id = "ghl-contact-realid-001"
    call_event_id = str(uuid.uuid4())
    call_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    mock_session = MagicMock()
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.payload_json = {
        "call_id": call_id,
        "call_event_id": call_event_id,
        "contact_id": ghl_id,
    }

    mock_call_event = MagicMock()
    mock_call_event.contact_id = ghl_id
    mock_call_event.transcript = "transcript text"
    mock_call_event.duration_seconds = 120
    mock_call_event.raw_payload_json = {"campaign_name": "New Lead"}

    mock_lead = MagicMock()
    mock_lead.normalized_phone = "+15550000001"

    mock_session.get.return_value = mock_call_event

    mock_ghl = MagicMock()
    mock_ghl.get_contact.return_value = {"contact": {"name": "Test Lead", "tags": []}}
    mock_ghl.update_contact_fields.return_value = {"shadow": True}

    mock_analysis = MagicMock()
    mock_analysis.lead_classification = "Interested"
    mock_analysis.is_lead_classification = True
    mock_analysis.assign_to = None
    mock_analysis.task_description = "Follow up"
    mock_analysis.task_title = "Call"
    mock_analysis.task_due_date = "2026-04-15"
    mock_analysis.ai_campaign = "Yes"
    mock_analysis.create_task = False
    mock_analysis.call_detailed_summary = "Lead is interested."
    mock_analysis.call_start_time_formatted = "2026-04-10 10:00"

    settings = MagicMock()
    settings.ghl_field_mark_as_lead = "mark_as_lead"
    settings.ghl_field_ai_lead_assign_to = None
    settings.ghl_field_support_ticket_3 = None
    settings.ghl_field_ai_lead_classification = None
    settings.ghl_field_ai_campaign = "ai_campaign"

    with (
        patch("app.worker.jobs.crm_jobs.claim_job", return_value=mock_job),
        patch("app.worker.jobs.crm_jobs.mark_running"),
        patch("app.worker.jobs.crm_jobs.complete_job"),
        patch("app.worker.jobs.crm_jobs.get_worker_id", return_value="w1"),
        patch("app.worker.jobs.crm_jobs.get_settings", return_value=settings),
        patch("app.worker.jobs.crm_jobs.get_sync_session") as mock_ctx,
        patch("app.adapters.ghl.GHLClient", return_value=mock_ghl),
        patch("app.core.ai_message_generator.generate_ghl_call_analysis", return_value=mock_analysis),
        patch("app.worker.jobs.crm_jobs._persist_ghl_analysis"),
    ):
        mock_ctx.return_value.__enter__ = lambda _: mock_session
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.scalars.return_value.first.side_effect = [mock_lead, None]

        create_crm_task(job_id)

    mock_ghl.get_contact.assert_called_once_with(ghl_id)
    mock_ghl.search_contact_by_phone.assert_not_called()
