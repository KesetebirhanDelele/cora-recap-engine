"""
Intent action handler — deterministic actions triggered by detected intents.

Called from process_voicemail_tier when a non-empty transcript yields a
recognised intent.  The caller is responsible for calling complete_job()
after this function returns.

For every intent:
  1. Cancel all other pending/claimed jobs for the contact (retry override).
  2. Apply the intent-specific action (update lead_state, schedule jobs, etc.).

Cancellation key: payload_json->>'contact_id' = contact_id.
The current voicemail-tier job is excluded from cancellation so the caller
can still mark it completed.

Nurture delay default: 7 days (NURTURE_DELAY_DAYS).
Callback fallback delay: 2 hours (CALLBACK_FALLBACK_MINUTES).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Policy constants (override via settings fields if needed)
CALLBACK_FALLBACK_MINUTES: int = 120    # 2 h default when no time was extracted
NURTURE_DELAY_DAYS: int = 7             # days before nurture outbound call


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def handle_intent(
    session: Session,
    intent_result: dict[str, Any],
    contact_id: str,
    phone: str,
    current_job_id: str,
    settings: Any,
) -> None:
    """
    Execute the deterministic action for a detected intent.

    Does NOT call complete_job() — that is the caller's responsibility.
    """
    intent = intent_result["intent"]
    entities = intent_result.get("entities", {})

    logger.info(
        "handle_intent | contact_id=%s intent=%s confidence=%.2f",
        contact_id, intent, intent_result.get("confidence", 0.0),
    )

    # 1. Cancel all other pending jobs for this contact (retry override)
    cancelled = _cancel_contact_jobs(session, contact_id, exclude_job_id=current_job_id)
    if cancelled:
        logger.info(
            "handle_intent: cancelled %d pending job(s) | contact_id=%s intent=%s",
            cancelled, contact_id, intent,
        )

    # 2. Apply intent-specific action
    _HANDLERS[intent](
        session=session,
        contact_id=contact_id,
        phone=phone,
        entities=entities,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Per-intent handlers
# ---------------------------------------------------------------------------

def _handle_callback_request(session, contact_id, phone, entities, settings) -> None:
    """Schedule a callback at now + CALLBACK_FALLBACK_MINUTES."""
    run_at = datetime.now(tz=timezone.utc) + timedelta(minutes=CALLBACK_FALLBACK_MINUTES)
    _schedule_outbound_call(session, contact_id, phone, run_at, settings,
                            reason="callback_request")


def _handle_callback_with_time(session, contact_id, phone, entities, settings) -> None:
    """Schedule a callback at the extracted datetime, fall back to 2 h if unresolved."""
    extracted: datetime | None = entities.get("datetime")
    if extracted is not None:
        extracted = _ensure_utc(extracted)

    if extracted is None:
        # Datetime extraction failed — use fallback and log so it is never silent
        logger.warning(
            "handle_intent: callback_with_time — datetime not extracted, "
            "using fallback +%dmin | contact_id=%s",
            CALLBACK_FALLBACK_MINUTES, contact_id,
        )

    run_at = extracted or (
        datetime.now(tz=timezone.utc) + timedelta(minutes=CALLBACK_FALLBACK_MINUTES)
    )
    _schedule_outbound_call(session, contact_id, phone, run_at, settings,
                            reason="callback_with_time")
    logger.info(
        "handle_intent: callback_with_time | contact_id=%s run_at=%s datetime_extracted=%s",
        contact_id, run_at.isoformat(), extracted is not None,
    )


def _handle_call_later_no_time(session, contact_id, phone, entities, settings) -> None:
    """Schedule a callback at fallback delay (same as callback_request)."""
    run_at = datetime.now(tz=timezone.utc) + timedelta(minutes=CALLBACK_FALLBACK_MINUTES)
    _schedule_outbound_call(session, contact_id, phone, run_at, settings,
                            reason="call_later_no_time")


def _handle_interested_not_now(session, contact_id, phone, entities, settings) -> None:
    """
    Move lead to nurture status.

    Sets lead_state.status = 'nurture' and next_action_at = now + nurture_delay_days.
    Does NOT schedule an outbound call here — the nurture scheduler will call
    enter_campaign("cold_lead") once next_action_at has passed.
    """
    delay_days = getattr(settings, "nurture_delay_days", NURTURE_DELAY_DAYS)
    run_at = datetime.now(tz=timezone.utc) + timedelta(days=delay_days)

    _update_lead_state(
        session, contact_id,
        status="nurture",
        next_action_at=run_at,
    )
    logger.info(
        "handle_intent: lead moved to nurture | contact_id=%s next_action_at=%s",
        contact_id, run_at.isoformat(),
    )


def _handle_uncertain(session, contact_id, phone, entities, settings) -> None:
    """
    Move an uncertain lead to nurture with a shorter follow-up window.

    Uses half of nurture_delay_days (minimum 1 day) to check back sooner
    than a fully disinterested lead.
    """
    base_days = getattr(settings, "nurture_delay_days", NURTURE_DELAY_DAYS)
    delay_days = max(1, base_days // 2)
    run_at = datetime.now(tz=timezone.utc) + timedelta(days=delay_days)
    _update_lead_state(
        session, contact_id,
        status="nurture",
        next_action_at=run_at,
    )
    logger.info(
        "handle_intent: uncertain lead moved to nurture | contact_id=%s "
        "next_action_at=%s delay_days=%d",
        contact_id, run_at.isoformat(), delay_days,
    )


def _handle_enrolled(session, contact_id, phone, entities, settings) -> None:
    """
    Lead confirmed enrollment — terminate the campaign permanently.

    Sets status='enrolled' and ai_campaign_value='3' so no voicemail tier
    or outbound call jobs will be scheduled again.  Writes AI Campaign='No'
    to GHL (shadow-gated) to mirror finalization.

    Distinct from 'closed' (not interested) so conversion reporting can
    separate successful enrollments from hard rejections.
    """
    _update_lead_state(
        session, contact_id,
        status="enrolled",
        ai_campaign_value="3",
    )
    logger.info(
        "ENROLLMENT CONFIRMED: campaign terminated | contact_id=%s", contact_id
    )

    # Mirror finalization in GHL (shadow-gated)
    try:
        from app.adapters.ghl import GHLClient
        ghl = GHLClient(settings=settings)
        ai_campaign_field = getattr(settings, "ghl_field_ai_campaign", None) or "AI Campaign"
        ghl.update_contact_fields(
            contact_id=contact_id,
            field_updates={ai_campaign_field: "No"},
        )
    except Exception as exc:
        logger.warning(
            "_handle_enrolled: GHL write failed (non-fatal) | contact_id=%s: %s",
            contact_id, exc,
        )


def _handle_re_engaged(session, contact_id, phone, entities, settings) -> None:
    """
    Cold lead expressed renewed interest — no status change, no scheduling.

    The campaign switch (Cold Lead → New Lead) is handled by the campaign
    switching hook in process_voicemail_tier immediately after handle_intent().
    This handler is a no-op; it exists only so _HANDLERS dispatch succeeds.
    """
    logger.info(
        "handle_intent: re_engaged — campaign switch will follow | contact_id=%s",
        contact_id,
    )


def _handle_not_interested(session, contact_id, phone, entities, settings) -> None:
    """Close the lead — stop all future outreach."""
    _update_lead_state(session, contact_id, status="closed")
    logger.info(
        "handle_intent: lead closed (not_interested) | contact_id=%s", contact_id
    )


def _handle_do_not_call(session, contact_id, phone, entities, settings) -> None:
    """Suppress all future calls by setting do_not_call = True."""
    _update_lead_state(session, contact_id, do_not_call=True, status="closed")
    logger.info(
        "handle_intent: do_not_call flag set | contact_id=%s", contact_id
    )


def _handle_wrong_number(session, contact_id, phone, entities, settings) -> None:
    """Mark lead as invalid — no further outreach."""
    _update_lead_state(session, contact_id, invalid=True, status="closed")
    logger.info(
        "handle_intent: wrong_number — lead marked invalid | contact_id=%s", contact_id
    )


def _handle_request_sms(session, contact_id, phone, entities, settings) -> None:
    """Set preferred channel to SMS and schedule an SMS stub job."""
    _update_lead_state(session, contact_id, preferred_channel="sms")
    _schedule_channel_job(session, contact_id, job_type="send_sms",
                          payload={"contact_id": contact_id, "phone": phone})
    logger.info(
        "handle_intent: SMS channel requested | contact_id=%s", contact_id
    )


def _handle_request_email(session, contact_id, phone, entities, settings) -> None:
    """Set preferred channel to email and schedule an email stub job."""
    _update_lead_state(session, contact_id, preferred_channel="email")
    _schedule_channel_job(session, contact_id, job_type="send_email",
                          payload={"contact_id": contact_id, "phone": phone})
    logger.info(
        "handle_intent: email channel requested | contact_id=%s", contact_id
    )


# Handler dispatch table (mirrors priority-ordered intent names)
_HANDLERS: dict[str, Any] = {
    "do_not_call":        _handle_do_not_call,
    "wrong_number":       _handle_wrong_number,
    "not_interested":     _handle_not_interested,
    "enrolled":           _handle_enrolled,
    "re_engaged":         _handle_re_engaged,
    "callback_with_time": _handle_callback_with_time,
    "callback_request":   _handle_callback_request,
    "interested_not_now": _handle_interested_not_now,
    "call_later_no_time": _handle_call_later_no_time,
    "uncertain":          _handle_uncertain,
    "request_sms":        _handle_request_sms,
    "request_email":      _handle_request_email,
}


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _ensure_utc(dt: datetime) -> datetime:
    """Return dt as UTC-aware. Attaches UTC if naive; converts if another tz."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _cancel_contact_jobs(
    session: Session, contact_id: str, exclude_job_id: str
) -> int:
    """
    Cancel all pending/claimed/running scheduled_jobs for a contact.

    Looks up jobs via payload_json->>'contact_id' so it catches jobs whose
    entity_id is a call_id rather than a contact_id.  Excludes the current
    voicemail-tier job so the caller can still mark it completed.

    Returns the number of rows cancelled.
    """
    from sqlalchemy import update

    from app.models.scheduled_job import ScheduledJob

    now = datetime.now(tz=timezone.utc)
    result = session.execute(
        update(ScheduledJob)
        .where(
            ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
            ScheduledJob.status.in_(["pending", "claimed", "running"]),
            ScheduledJob.id != exclude_job_id,
        )
        .values(status="cancelled", updated_at=now)
    )
    session.flush()
    return result.rowcount


def _update_lead_state(
    session: Session,
    contact_id: str,
    *,
    status: str | None = None,
    do_not_call: bool | None = None,
    invalid: bool | None = None,
    preferred_channel: str | None = None,
    next_action_at: datetime | None = None,
    ai_campaign_value: str | None = None,
) -> None:
    """
    Apply intent-driven field updates to the lead_state row for contact_id.

    Uses optimistic concurrency (version increment).  If the row is missing,
    logs a warning and skips — creation is handled earlier in the pipeline.
    """
    from sqlalchemy import select, update

    from app.models.lead_state import LeadState

    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()

    if lead is None:
        logger.warning(
            "_update_lead_state: no lead_state row for contact_id=%s — skipping",
            contact_id,
        )
        return

    updates: dict = {"version": lead.version + 1,
                     "updated_at": datetime.now(tz=timezone.utc)}
    if status is not None:
        updates["status"] = status
    if do_not_call is not None:
        updates["do_not_call"] = do_not_call
    if invalid is not None:
        updates["invalid"] = invalid
    if preferred_channel is not None:
        updates["preferred_channel"] = preferred_channel
    if next_action_at is not None:
        updates["next_action_at"] = next_action_at
    if ai_campaign_value is not None:
        updates["ai_campaign_value"] = ai_campaign_value

    session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(**updates)
    )
    session.flush()


def _schedule_outbound_call(
    session: Session,
    contact_id: str,
    phone: str,
    run_at: datetime,
    settings: Any,
    *,
    reason: str = "",
) -> None:
    """Create a launch_outbound_call scheduled_job record (idempotent)."""
    from sqlalchemy import select

    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import schedule_job

    existing = session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.payload_json["contact_id"].as_string() == contact_id,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed", "running"]),
        )
    ).first()
    if existing:
        logger.info(
            "_schedule_outbound_call: pending call already exists, skipping | "
            "contact_id=%s reason=%s",
            contact_id, reason,
        )
        return

    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload={
            "contact_id": contact_id,
            "phone_number": phone,
            "intent_reason": reason,
        },
    )
    logger.info(
        "_schedule_outbound_call | contact_id=%s reason=%s run_at=%s",
        contact_id, reason, run_at.isoformat(),
    )


def _schedule_channel_job(
    session: Session,
    contact_id: str,
    job_type: str,
    payload: dict,
) -> None:
    """Schedule a channel-delivery stub job (SMS / email)."""
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type=job_type,
        entity_type="lead",
        entity_id=contact_id,
        run_at=datetime.now(tz=timezone.utc),
        payload=payload,
    )
    logger.warning(
        "_schedule_channel_job: job_type=%s scheduled but no handler registered yet | "
        "contact_id=%s",
        job_type, contact_id,
    )
