"""
E2E seed data generator.

Creates minimal, production-representative rows for lead_state and
scheduled_jobs so each scenario starts from a clean, known state.

Design rules:
  - Only two campaigns: "New Lead" and "Cold Lead"
  - Phone numbers follow E.164 format
  - All tier values are canonical: None | '0' | '1' | '2'
  - Jobs are created in 'pending' status with realistic payloads
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Lead state seeds
# ---------------------------------------------------------------------------

def seed_new_lead(
    session: Session,
    *,
    contact_id: str | None = None,
    phone: str = "+15550001001",
    tier: str | None = None,
    status: str = "active",
) -> LeadState:
    """
    Seed a New Lead.

    tier: current ai_campaign_value (None = no voicemail yet, '0'/'1'/'2' = mid-sequence)
    """
    contact_id = contact_id or str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        campaign_name="New Lead",
        ai_campaign_value=tier,
        status=status,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


def seed_cold_lead(
    session: Session,
    *,
    contact_id: str | None = None,
    phone: str = "+15550002001",
    tier: str | None = None,
    status: str = "active",
) -> LeadState:
    """
    Seed a Cold Lead.

    tier: current ai_campaign_value (None = no voicemail yet, '0'/'1'/'2' = mid-sequence)
    """
    contact_id = contact_id or str(uuid.uuid4())
    lead = LeadState(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        normalized_phone=phone,
        campaign_name="Cold Lead",
        ai_campaign_value=tier,
        status=status,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(lead)
    session.flush()
    return lead


# ---------------------------------------------------------------------------
# Call event seeds
# ---------------------------------------------------------------------------

def seed_call_event(
    session: Session,
    *,
    call_id: str | None = None,
    contact_id: str,
    status: str = "voicemail",
    transcript: str | None = None,
) -> CallEvent:
    """Seed a CallEvent row for a previously completed call."""
    call_id = call_id or f"call-{uuid.uuid4().hex[:8]}"
    dedupe_key = f"{call_id}:process_call_event"
    event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=contact_id,
        direction="outbound",
        status=status,
        transcript=transcript,
        dedupe_key=dedupe_key,
        created_at=_now(),
    )
    session.add(event)
    session.flush()
    return event


# ---------------------------------------------------------------------------
# Scheduled job seeds
# ---------------------------------------------------------------------------

def seed_process_call_event_job(
    session: Session,
    *,
    call_id: str | None = None,
    contact_id: str,
    call_status: str = "voicemail",
    campaign_name: str = "New Lead",
    lead_name: str = "Test Lead",
    transcript: str | None = None,
) -> ScheduledJob:
    """
    Seed a pending process_call_event job, mimicking a real Synthflow webhook.

    call_status: 'completed' → call-through path; 'voicemail' → voicemail path.
    transcript:  optional — triggers intent detection in process_voicemail_tier.
    """
    call_id = call_id or f"call-{uuid.uuid4().hex[:8]}"
    payload: dict = {
        "call_id": call_id,
        "contact_id": contact_id,
        "call_status": call_status,
        "campaign_name": campaign_name,
        "lead_name": lead_name,
    }
    if transcript is not None:
        payload["transcript"] = transcript

    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_call_event",
        entity_type="call",
        entity_id=call_id,
        status="pending",
        run_at=_now(),
        payload_json=payload,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(job)
    session.flush()
    return job


def seed_process_voicemail_tier_job(
    session: Session,
    *,
    call_id: str | None = None,
    call_event_id: str | None = None,
    contact_id: str,
    campaign_name: str = "New Lead",
    lead_name: str = "Test Lead",
) -> ScheduledJob:
    """
    Seed a pending process_voicemail_tier job directly (skip process_call_event).

    Useful for scenarios that only need to test voicemail tier advancement.
    """
    call_id = call_id or f"call-{uuid.uuid4().hex[:8]}"
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_voicemail_tier",
        entity_type="call",
        entity_id=call_id,
        status="pending",
        run_at=_now(),
        payload_json={
            "call_id": call_id,
            "contact_id": contact_id,
            "call_event_id": call_event_id,
            "campaign_name": campaign_name,
            "lead_name": lead_name,
        },
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(job)
    session.flush()
    return job
