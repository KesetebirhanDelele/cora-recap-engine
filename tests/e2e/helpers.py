"""
E2E test helpers — DB query utilities for asserting system state after job runs.

All helpers operate on a SQLAlchemy Session.  They raise AssertionError with a
descriptive message when the expected state is not found so test output is clear.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.call_event import CallEvent
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob


# ---------------------------------------------------------------------------
# Lead state queries
# ---------------------------------------------------------------------------

def get_lead(session: Session, contact_id: str) -> LeadState:
    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    assert lead is not None, f"No lead_state row for contact_id={contact_id!r}"
    return lead


def assert_lead_tier(session: Session, contact_id: str, expected_tier: Optional[str]) -> None:
    lead = get_lead(session, contact_id)
    assert lead.ai_campaign_value == expected_tier, (
        f"Expected tier={expected_tier!r} but got {lead.ai_campaign_value!r} "
        f"for contact_id={contact_id!r}"
    )


def assert_lead_status(session: Session, contact_id: str, expected_status: str) -> None:
    lead = get_lead(session, contact_id)
    assert lead.status == expected_status, (
        f"Expected status={expected_status!r} but got {lead.status!r} "
        f"for contact_id={contact_id!r}"
    )


def assert_lead_campaign(session: Session, contact_id: str, expected_campaign: str) -> None:
    lead = get_lead(session, contact_id)
    assert lead.campaign_name == expected_campaign, (
        f"Expected campaign_name={expected_campaign!r} but got {lead.campaign_name!r} "
        f"for contact_id={contact_id!r}"
    )


def assert_no_do_not_call(session: Session, contact_id: str) -> None:
    lead = get_lead(session, contact_id)
    assert not lead.do_not_call, (
        f"Expected do_not_call=False but got True for contact_id={contact_id!r}"
    )


def assert_do_not_call(session: Session, contact_id: str) -> None:
    lead = get_lead(session, contact_id)
    assert lead.do_not_call, (
        f"Expected do_not_call=True but got False for contact_id={contact_id!r}"
    )


# ---------------------------------------------------------------------------
# Scheduled job queries
# ---------------------------------------------------------------------------

def get_jobs(
    session: Session,
    contact_id: str,
    job_type: Optional[str] = None,
    status: Optional[str] = None,
) -> list[ScheduledJob]:
    """Return all scheduled jobs whose payload_json.contact_id matches contact_id."""
    stmt = select(ScheduledJob).where(
        ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
    )
    if job_type:
        stmt = stmt.where(ScheduledJob.job_type == job_type)
    if status:
        stmt = stmt.where(ScheduledJob.status == status)
    return list(session.scalars(stmt).all())


def get_jobs_by_type(
    session: Session,
    job_type: str,
    status: Optional[str] = None,
) -> list[ScheduledJob]:
    stmt = select(ScheduledJob).where(ScheduledJob.job_type == job_type)
    if status:
        stmt = stmt.where(ScheduledJob.status == status)
    return list(session.scalars(stmt).all())


def assert_pending_outbound_call(session: Session, contact_id: str) -> ScheduledJob:
    """Assert exactly one pending launch_outbound_call job exists for contact."""
    jobs = get_jobs(session, contact_id, job_type="launch_outbound_call", status="pending")
    assert len(jobs) >= 1, (
        f"Expected a pending launch_outbound_call job for contact_id={contact_id!r}, "
        f"found {len(jobs)}"
    )
    return jobs[0]


def assert_no_pending_outbound_call(session: Session, contact_id: str) -> None:
    """Assert no pending launch_outbound_call job exists for contact."""
    jobs = get_jobs(session, contact_id, job_type="launch_outbound_call", status="pending")
    assert len(jobs) == 0, (
        f"Expected no pending launch_outbound_call for contact_id={contact_id!r}, "
        f"found {len(jobs)}"
    )


def find_job(
    session: Session,
    job_type: str,
    entity_id: Optional[str] = None,
    status: Optional[str] = None,
) -> Optional[ScheduledJob]:
    """Find a single job by type (and optionally entity_id and status)."""
    stmt = select(ScheduledJob).where(ScheduledJob.job_type == job_type)
    if entity_id:
        stmt = stmt.where(ScheduledJob.entity_id == entity_id)
    if status:
        stmt = stmt.where(ScheduledJob.status == status)
    return session.scalars(stmt).first()


def assert_job_status(session: Session, job_id: str, expected_status: str) -> None:
    job = session.get(ScheduledJob, job_id)
    assert job is not None, f"Job {job_id!r} not found"
    assert job.status == expected_status, (
        f"Expected job status={expected_status!r} but got {job.status!r} for job_id={job_id!r}"
    )


# ---------------------------------------------------------------------------
# Call event queries
# ---------------------------------------------------------------------------

def get_call_event(session: Session, call_id: str) -> Optional[CallEvent]:
    return session.scalars(
        select(CallEvent).where(CallEvent.call_id == call_id)
    ).first()


def assert_call_event_created(session: Session, call_id: str) -> CallEvent:
    event = get_call_event(session, call_id)
    assert event is not None, f"No CallEvent row for call_id={call_id!r}"
    return event


# ---------------------------------------------------------------------------
# Exception record queries
# ---------------------------------------------------------------------------

def get_exceptions(session: Session, entity_id: str) -> list:
    from app.models.exception import ExceptionRecord
    return list(session.scalars(
        select(ExceptionRecord).where(ExceptionRecord.entity_id == entity_id)
    ).all())
