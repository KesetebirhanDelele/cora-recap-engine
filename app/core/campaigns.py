"""
Campaign entry — places a lead into a named outreach campaign.

enter_campaign() is the single public entry point.  It:
  1. Cancels all pending/claimed jobs for the lead (clean slate).
  2. Resets the voicemail tier counter to None (fresh progression).
  3. Updates campaign_name so tier_policy picks up the right delay schedule.
  4. Schedules a launch_outbound_call job to make the first call.

Supported campaign types
------------------------
  "cold_lead"  — Cold Lead voicemail tier campaign.
                 Sets campaign_name = "Cold Lead" (matches tier_policy key).
                 Uses cold_vm_tier_* delay settings.
  "new_lead"   — New Lead voicemail tier campaign (initial inbound).
                 Sets campaign_name = "New Lead" (matches tier_policy key).
                 Uses new_vm_tier_* delay settings.
Idempotency
-----------
  If a pending launch_outbound_call already exists for this contact after
  cancellation, a duplicate is not created.  (Cancellation itself should
  remove any prior pending calls, so this is a belt-and-suspenders guard.)

Lead name
---------
  LeadState does not store the contact's display name — it is only present in
  call event payloads.  Campaign-entry calls will use an empty lead_name,
  which causes Synthflow to default to "Customer".
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Maps campaign_type slug → canonical campaign_name stored in lead_state.campaign_name
# and passed to tier_policy.get_tier_policy().
_CAMPAIGN_NAMES: dict[str, str] = {
    "cold_lead": "Cold Lead",
    "new_lead":  "New Lead",
}

# Campaign switch rules: (current_campaign_name_lower, intent) → new_campaign_name
#
# New Lead → Cold Lead: lead is cooling off (not ready, uncertain)
# Cold Lead → New Lead: lead expresses genuine interest (re_engaged only)
#
# Callback/call-later intents are intentionally excluded — they signal the lead
# wants to engage at a better time, not a change in engagement level.
_SWITCH_RULES: dict[tuple[str, str], str] = {
    ("new lead", "interested_not_now"): "Cold Lead",
    ("new lead", "uncertain"):          "Cold Lead",
    ("cold lead", "re_engaged"):        "New Lead",
}


def enter_campaign(
    session: Session,
    lead: Any,
    campaign_type: str,
    settings: Any = None,
) -> None:
    """
    Place a lead into the named campaign.

    Idempotent: safe to call multiple times for the same lead — duplicate
    outbound calls are suppressed by the pending-job check.
    """
    from sqlalchemy import update

    from app.models.lead_state import LeadState
    from app.worker.scheduler import schedule_job

    campaign_name = _CAMPAIGN_NAMES.get(campaign_type)
    if campaign_name is None:
        logger.error(
            "enter_campaign: unknown campaign_type=%r | contact_id=%s",
            campaign_type, lead.contact_id,
        )
        return

    # 1. Cancel existing pending jobs (clean slate for the new campaign)
    cancelled = _cancel_pending_jobs(session, lead.contact_id)
    if cancelled:
        logger.info(
            "enter_campaign: cancelled %d pending job(s) | contact_id=%s campaign=%s",
            cancelled, lead.contact_id, campaign_type,
        )

    # 2 + 3. Reset voicemail tier and set campaign name
    now = datetime.now(tz=timezone.utc)
    session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(
            ai_campaign_value=None,
            campaign_name=campaign_name,
            version=lead.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    session.refresh(lead)

    # 4. Schedule first outbound call (idempotent)
    phone = lead.normalized_phone or ""
    if not phone:
        logger.warning(
            "enter_campaign: no phone number — outbound call not scheduled | "
            "contact_id=%s campaign=%s",
            lead.contact_id, campaign_type,
        )
        return

    if _has_pending_outbound(session, lead.contact_id):
        logger.info(
            "enter_campaign: pending outbound already exists, skipping | "
            "contact_id=%s campaign=%s",
            lead.contact_id, campaign_type,
        )
        return

    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=lead.contact_id,
        run_at=now,
        payload={
            "contact_id": lead.contact_id,
            "phone_number": phone,
            "lead_name": "",         # not stored on lead_state; Synthflow defaults to "Customer"
            "campaign_name": campaign_name,
            "source": f"campaign_entry:{campaign_type}",
        },
    )
    logger.info(
        "enter_campaign: first outbound scheduled | contact_id=%s campaign=%s",
        lead.contact_id, campaign_type,
    )


# ---------------------------------------------------------------------------
# Campaign switching
# ---------------------------------------------------------------------------

def evaluate_campaign_switch(campaign_name: str, intent: str) -> str | None:
    """
    Return the new campaign name if the intent warrants a switch, else None.

    Pure function — no DB access.  Callers decide whether to apply the result.

    Rules:
      New Lead + interested_not_now → Cold Lead  (lead cooling off)
      New Lead + uncertain          → Cold Lead  (lead cooling off)
      Cold Lead + re_engaged        → New Lead   (lead expressing genuine interest)
    """
    key = ((campaign_name or "").strip().lower(), intent)
    return _SWITCH_RULES.get(key)


def apply_campaign_switch(
    session: Session,
    lead: Any,
    new_campaign_name: str,
    *,
    reason: str,
) -> None:
    """
    Update campaign_name in place — lightweight field update only.

    Does NOT reset the voicemail tier, cancel jobs, or schedule new calls.
    The tier continues from its current position using the new campaign's
    delay policy on the next voicemail job.

    Uses optimistic concurrency (version increment).
    """
    from sqlalchemy import update

    from app.models.lead_state import LeadState

    old_campaign = lead.campaign_name
    now = datetime.now(tz=timezone.utc)

    result = session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(
            campaign_name=new_campaign_name,
            version=lead.version + 1,
            updated_at=now,
        )
    )
    session.flush()

    if result.rowcount == 0:
        logger.warning(
            "apply_campaign_switch: version conflict, switch not applied | "
            "contact_id=%s reason=%s",
            lead.contact_id, reason,
        )
        return

    logger.info(
        "CAMPAIGN SWITCH: %r → %r | contact_id=%s reason=%s",
        old_campaign, new_campaign_name, lead.contact_id, reason,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _cancel_pending_jobs(session: Session, contact_id: str) -> int:
    """Cancel all pending/claimed/running jobs whose payload references this contact."""
    from sqlalchemy import update

    from app.models.scheduled_job import ScheduledJob

    now = datetime.now(tz=timezone.utc)
    result = session.execute(
        update(ScheduledJob)
        .where(
            ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
            ScheduledJob.status.in_(["pending", "claimed", "running"]),
        )
        .values(status="cancelled", updated_at=now)
    )
    session.flush()
    return result.rowcount


def _has_pending_outbound(session: Session, contact_id: str) -> bool:
    """Return True if a pending launch_outbound_call already exists for this contact."""
    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob

    existing = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed", "running"]),
        )
    ).first()
    return existing is not None
