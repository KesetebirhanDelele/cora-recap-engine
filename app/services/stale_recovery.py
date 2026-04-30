"""
Manual recovery for stale leads whose Synthflow webhooks were missed.

outcome="voicemail" — VM was left; advance tier or finalize if tier 2.
outcome="no_answer" — call did not connect; retry same tier, or close if
                      lead_state.last_call_status was already a no-answer
                      status (consecutive failure signals terminal).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.mode_flags import get_mode_flags
from app.models.audit import AuditLog
from app.models.scheduled_job import ScheduledJob
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

_NO_ANSWER_STATUSES = frozenset({
    "no_answer", "failed", "busy", "cancelled", "timeout", "error",
})

_CALL_BATCH_SIZE = 4
_CALL_SLOT_SECONDS = 300
_CALL_WITHIN_SLOT_SPACING = 75  # 300 // 4


class StaleLeadConflict(Exception):
    pass


class StaleLeadNotFound(Exception):
    pass


def advance_stale_lead(
    session: Session,
    contact_id: str,
    outcome: str,
    operator_id: str,
    settings: Settings,
) -> dict[str, Any]:
    """
    Manually advance a stale lead whose Synthflow webhook was missed.

    outcome="voicemail" — operator confirmed VM was left in Synthflow logs.
    outcome="no_answer" — operator confirmed call did not connect.
    """
    lead = session.execute(
        text("SELECT * FROM lead_state WHERE contact_id = :id"),
        {"id": contact_id},
    ).fetchone()
    if not lead:
        raise StaleLeadNotFound(f"Lead not found: {contact_id}")

    # Idempotency — abort if a job arrived while operator was checking Synthflow
    pending = session.execute(
        text(
            "SELECT 1 FROM scheduled_jobs"
            " WHERE entity_id = :id AND status IN ('pending','claimed','running')"
            " LIMIT 1"
        ),
        {"id": contact_id},
    ).fetchone()
    if pending:
        raise StaleLeadConflict(
            "Lead already has a pending job — action cancelled to avoid duplicate"
        )

    tier = lead.ai_campaign_value
    campaign_name = lead.campaign_name or "Cold Lead"
    last_call_status = (lead.last_call_status or "").lower()

    if outcome == "voicemail":
        return _handle_voicemail(session, lead, tier, campaign_name, operator_id, settings)
    elif outcome == "no_answer":
        return _handle_no_answer(
            session, lead, tier, campaign_name, last_call_status, operator_id, settings
        )
    else:
        raise ValueError(f"Invalid outcome: {outcome!r} — must be 'voicemail' or 'no_answer'")


def _handle_voicemail(session, lead, tier, campaign_name, operator_id, settings):
    contact_id = lead.contact_id

    if tier == "2":
        _finalize_lead(session, contact_id, campaign_name, operator_id, settings)
        return {"action": "finalized", "tier_from": tier, "reason": "tier_2_voicemail_complete"}

    from app.services.tier_policy import get_tier_policy

    next_tier = str((int(tier or "0")) + 1)
    policy = get_tier_policy(campaign_name, tier or "0", settings)
    run_at = datetime.now(timezone.utc) + timedelta(minutes=policy.delay_minutes)

    session.execute(
        text(
            "UPDATE lead_state"
            " SET ai_campaign_value = :tier, last_call_status = 'voicemail',"
            "     version = version + 1, updated_at = NOW()"
            " WHERE contact_id = :id"
        ),
        {"tier": next_tier, "id": contact_id},
    )

    last_payload = _last_payload(session, contact_id)
    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload=_build_payload(last_payload, contact_id, campaign_name),
    )

    audit_id = _write_audit(session, contact_id, operator_id, "manual_advance", {
        "reason": "stale_webhook_recovery",
        "outcome": "voicemail",
        "tier_from": tier,
        "tier_to": next_tier,
    })
    logger.info(
        "stale_advance | contact_id=%s voicemail tier %s->%s run_at=%s",
        contact_id, tier, next_tier, run_at.isoformat(),
    )
    return {
        "action": "advanced", "tier_from": tier, "tier_to": next_tier,
        "run_at": run_at.isoformat(), "audit_log_id": audit_id,
    }


def _handle_no_answer(session, lead, tier, campaign_name, last_call_status, operator_id, settings):
    contact_id = lead.contact_id

    if last_call_status in _NO_ANSWER_STATUSES:
        # Consecutive no-answer — close campaign
        _close_lead(session, contact_id, campaign_name, operator_id, settings)
        return {
            "action": "closed",
            "reason": "consecutive_no_answer",
            "last_call_status": last_call_status,
        }

    # First no-answer — retry; record status so next no-answer closes
    session.execute(
        text(
            "UPDATE lead_state SET last_call_status = 'no_answer',"
            " version = version + 1, updated_at = NOW()"
            " WHERE contact_id = :id"
        ),
        {"id": contact_id},
    )

    run_at = _compute_run_at(session)
    last_payload = _last_payload(session, contact_id)
    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload=_build_payload(last_payload, contact_id, campaign_name),
    )

    audit_id = _write_audit(session, contact_id, operator_id, "manual_advance", {
        "reason": "stale_webhook_recovery",
        "outcome": "no_answer",
        "tier": tier,
        "action": "retry_scheduled",
    })
    logger.info(
        "stale_advance | contact_id=%s no_answer tier=%s run_at=%s (retry)",
        contact_id, tier, run_at.isoformat(),
    )
    return {
        "action": "retry_scheduled", "tier": tier,
        "run_at": run_at.isoformat(), "audit_log_id": audit_id,
    }


def _finalize_lead(session, contact_id, campaign_name, operator_id, settings):
    from app.adapters.ghl import GHLClient
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids
    from app.worker.shadow import log_shadow_action

    session.execute(
        text(
            "UPDATE lead_state"
            " SET status = 'terminal', ai_campaign_value = '3',"
            "     last_call_status = 'voicemail',"
            "     version = version + 1, updated_at = NOW()"
            " WHERE contact_id = :id"
        ),
        {"id": contact_id},
    )

    flags = get_mode_flags(session, settings)
    ghl = GHLClient(settings=settings)
    field_updates: dict[str, str] = {}
    if settings.ghl_field_ai_campaign:
        field_updates[settings.ghl_field_ai_campaign] = "No"
    if settings.ghl_field_mark_as_lead:
        field_updates[settings.ghl_field_mark_as_lead] = "Yes"
    if settings.ghl_field_ai_campaign_value:
        field_updates[settings.ghl_field_ai_campaign_value] = "3"

    if field_updates:
        resolved = _resolve_to_field_ids(ghl, field_updates)
        if resolved:
            ghl.update_contact_fields(contact_id, resolved, flags)
        else:
            log_shadow_action(session, contact_id, "ghl_finalize_skipped",
                              {"reason": "field_resolution_failed"})

    _write_audit(session, contact_id, operator_id, "manual_advance", {
        "reason": "stale_webhook_recovery",
        "outcome": "voicemail",
        "tier_from": "2", "tier_to": "3", "action": "finalized",
    })
    logger.info("stale_advance | contact_id=%s voicemail tier 2->3 FINALIZED", contact_id)


def _close_lead(session, contact_id, campaign_name, operator_id, settings):
    from app.adapters.ghl import GHLClient
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids
    from app.worker.shadow import log_shadow_action

    session.execute(
        text(
            "UPDATE lead_state SET status = 'closed',"
            " version = version + 1, updated_at = NOW()"
            " WHERE contact_id = :id"
        ),
        {"id": contact_id},
    )

    flags = get_mode_flags(session, settings)
    ghl = GHLClient(settings=settings)
    field_updates: dict[str, str] = {}
    if settings.ghl_field_ai_campaign:
        field_updates[settings.ghl_field_ai_campaign] = "No"

    if field_updates:
        resolved = _resolve_to_field_ids(ghl, field_updates)
        if resolved:
            ghl.update_contact_fields(contact_id, resolved, flags)
        else:
            log_shadow_action(session, contact_id, "ghl_close_skipped",
                              {"reason": "field_resolution_failed"})

    _write_audit(session, contact_id, operator_id, "manual_advance", {
        "reason": "stale_webhook_recovery",
        "outcome": "no_answer", "action": "closed",
    })
    logger.info("stale_advance | contact_id=%s consecutive_no_answer -> CLOSED", contact_id)


def _compute_run_at(session: Session) -> datetime:
    window_start = datetime.now(timezone.utc)
    pending = session.scalar(
        select(func.count()).select_from(ScheduledJob).where(
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status.in_(["pending", "claimed"]),
            ScheduledJob.run_at >= window_start,
            ScheduledJob.run_at < window_start + timedelta(hours=4),
        )
    ) or 0
    slot = pending // _CALL_BATCH_SIZE
    within_slot = (pending % _CALL_BATCH_SIZE) * _CALL_WITHIN_SLOT_SPACING
    return window_start + timedelta(seconds=slot * _CALL_SLOT_SECONDS + within_slot)


def _last_payload(session: Session, contact_id: str) -> dict:
    row = session.execute(
        text(
            "SELECT payload_json FROM scheduled_jobs"
            " WHERE entity_id = :id AND status IN ('completed','failed','cancelled')"
            " ORDER BY run_at DESC LIMIT 1"
        ),
        {"id": contact_id},
    ).fetchone()
    return row.payload_json if row and row.payload_json else {}


def _build_payload(last_payload: dict, contact_id: str, campaign_name: str) -> dict:
    return {
        "phone_number":   last_payload.get("phone_number") or contact_id,
        "lead_name":      last_payload.get("lead_name", ""),
        "campaign_name":  last_payload.get("campaign_name") or campaign_name,
        "contact_id":     contact_id,
        "source":         "manual_stale_advance",
        "correlation_id": last_payload.get("correlation_id", contact_id),
    }


def _write_audit(
    session: Session, contact_id: str, operator_id: str, action: str, context: dict
) -> str:
    audit_id = str(uuid.uuid4())
    session.add(AuditLog(
        id=audit_id,
        entity_type="lead",
        entity_id=contact_id,
        action=action,
        operator_id=operator_id,
        context_json=context,
    ))
    return audit_id
