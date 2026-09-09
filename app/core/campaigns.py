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

Call spacing
------------
  The first-touch job's run_at is slot-aware (see spec/21) — it is placed on
  the same shared 75-second bucket grid voicemail-tier retries already use
  (voicemail_jobs.py::_slot_aware_run_at), not scheduled at a bare `now`.
  This closes a gap where multiple leads entering a campaign close together
  (e.g. nurture_scheduler.py's up-to-50-lead batch) could previously produce
  several launch_outbound_call jobs with near-identical run_at and no
  collision protection between them.
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
#
# Guard: these switches are only applied to leads that are NOT currently in an
# active voicemail tier sequence.  A lead mid-sequence (ai_campaign_value not
# None and not terminal "3") maintains its campaign_name until the sequence
# ends.  The guard is enforced in ai_jobs.py before calling apply_campaign_switch.
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
    from app.worker.jobs.voicemail_jobs import _slot_aware_run_at
    from app.worker.scheduler import schedule_job

    campaign_name = _CAMPAIGN_NAMES.get(campaign_type)
    if campaign_name is None:
        logger.error(
            "enter_campaign: unknown campaign_type=%r | contact_id=%s",
            campaign_type, lead.contact_id,
        )
        return

    # ── Outbound campaign pause check ───────────────────────────────────────
    # Centralized here (not duplicated per-caller) so every current and future
    # entry point into cold_lead/new_lead is protected uniformly — this is the
    # same check nurture_scheduler.py applies before calling enter_campaign();
    # intent_actions.py's direct calls previously bypassed it entirely.
    if settings is not None:
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.outbound_campaigns_paused:
            logger.info(
                "enter_campaign: outbound campaigns paused — skipping entry | "
                "contact_id=%s campaign=%s", lead.contact_id, campaign_name,
            )
            return
        if campaign_type == "cold_lead" and flags.cold_lead_campaign_paused:
            logger.info(
                "enter_campaign: cold lead campaign paused — skipping entry | "
                "contact_id=%s campaign=%s", lead.contact_id, campaign_name,
            )
            return

    # ── Do-not-call guard ────────────────────────────────────────────────────
    # lead_state.do_not_call is set by handle_intent() from live-call
    # detection but was never checked before dialing — a previously-known,
    # separately-tracked gap (see PROGRESS.md, 2026-07-15 session) closed here.
    # Logged, not raised as an exception: GHL re-enrolls already-worked /
    # do_not_call contacts routinely (its Cold Lead list has no way to know
    # Cora flagged them), so a suppression here is expected list churn, not an
    # anomaly worth a dashboard alert. See PROGRESS.md 2026-09-09.
    if getattr(lead, "do_not_call", False):
        logger.info(
            "enter_campaign: do_not_call set — skipping entry | contact_id=%s campaign=%s",
            lead.contact_id, campaign_name,
        )
        return

    # ── Urgent-escalation guard ──────────────────────────────────────────────
    # A lead who recently escalated to a human or had a callback/appointment
    # booked must not be re-entered into an unrelated cold-pitch campaign
    # until a sales rep has triaged it. See app.core.escalation_guard.
    from app.core.escalation_guard import check_urgent_unresolved
    escalation = check_urgent_unresolved(session, lead.contact_id)
    if escalation is not None:
        logger.info(
            "enter_campaign: urgent unresolved escalation — skipping entry | "
            "contact_id=%s campaign=%s detected_intent=%s call_time=%s",
            lead.contact_id, campaign_name,
            escalation["detected_intent"], escalation["call_time"],
        )
        from app.worker.exceptions import create_exception
        create_exception(
            session,
            type="outbound_suppressed_urgent_escalation",
            severity="warning",
            context={
                "contact_id": lead.contact_id,
                "campaign_type": campaign_type,
                **escalation,
            },
            entity_type="lead",
            entity_id=lead.contact_id,
        )
        return

    # 1. Cancel existing pending jobs (clean slate for the new campaign)
    cancelled = _cancel_pending_jobs(session, lead.contact_id)
    if cancelled:
        logger.info(
            "enter_campaign: cancelled %d pending job(s) | contact_id=%s campaign=%s",
            cancelled, lead.contact_id, campaign_type,
        )

    # 2 + 3. Reset voicemail tier, set campaign name, and clear any stale
    # nurture-wait timestamp (see intent_actions.py / nurture_scheduler.py —
    # next_action_at is a one-shot "come back to this lead at this time"
    # marker; once a campaign entry has fired, whether from nurture graduating
    # or a fresh GHL trigger, its job is done. Left uncleared, it lingers
    # indefinitely and can outrank a genuinely-scheduled call in the
    # dashboard's Scheduled Actions view (LEAST(scheduled_job.run_at,
    # next_action_at) picks the older stale value), hiding real upcoming
    # calls from view — see PROGRESS.md 2026-08-24 for the incident this
    # was found from.
    now = datetime.now(tz=timezone.utc)
    session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(
            ai_campaign_value=None,
            campaign_name=campaign_name,
            next_action_at=None,
            version=lead.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    session.refresh(lead)

    # 4. Schedule first outbound call (idempotent)
    # Prefer normalized_phone, but fall back to contact_id when it's
    # phone-shaped — contact_id is the one field in this data model with a
    # guaranteed dialable-number invariant (see call_intake.py's
    # phone-derived-contact_id convention). normalized_phone can be missing
    # or, historically, corrupted by an inbound-call fallback bug (spec/24).
    phone = lead.normalized_phone or (
        lead.contact_id if lead.contact_id and lead.contact_id.startswith("+") else ""
    )
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
        run_at=_slot_aware_run_at(session, 0, campaign_name),
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
    Writes one audit_log row on success so Lead Journey can show campaign history.
    """
    import uuid

    from sqlalchemy import update

    from app.models.audit import AuditLog
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

    session.add(AuditLog(
        id=str(uuid.uuid4()),
        entity_type="lead",
        entity_id=lead.contact_id,
        action="campaign_switch",
        operator_id="system",
        context_json={"from": old_campaign, "to": new_campaign_name, "reason": reason},
        created_at=now,
    ))
    session.flush()

    # Publish dashboard event (non-fatal)
    try:
        from app.services.event_publisher import publish_event
        publish_event(
            session=session,
            event_type="campaign_switched",
            entity_type="lead",
            entity_id=lead.contact_id,
            contact_id=lead.contact_id,
            message=f"Campaign switch: {old_campaign!r} → {new_campaign_name!r}",
            payload={"from": old_campaign, "to": new_campaign_name, "reason": reason},
        )
    except Exception as _pub_exc:
        logger.debug("apply_campaign_switch: event publish skipped: %s", _pub_exc)


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
