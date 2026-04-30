"""
Manual recovery for stale leads whose Synthflow webhooks were missed.

outcome="voicemail" — VM was left; advance tier or finalize if tier 2.
outcome="no_answer" — call did not connect; retry same tier, or close if
                      lead_state.last_call_status was already a no-answer
                      status (consecutive failure signals terminal).

recover_missed_webhook() — operator provides the Synthflow call_id; fetches
                           the call from Synthflow API and re-runs the full
                           process_call_event pipeline exactly as if the
                           webhook had been delivered.
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


def _resolve_ghl_contact_id(ghl, contact_id: str) -> str | None:
    """Resolve a phone-based contact_id to real GHL UUID; return as-is if already a UUID."""
    stripped = contact_id.replace(" ", "").replace("-", "").replace("+", "")
    if stripped.isdigit():
        found = ghl.search_contact_by_phone(contact_id)
        return found.get("id") if found else None
    return contact_id


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
        resolved_fields = _resolve_to_field_ids(ghl, field_updates)
        ghl_id = _resolve_ghl_contact_id(ghl, contact_id)
        if resolved_fields and ghl_id:
            ghl.update_contact_fields(ghl_id, resolved_fields, mode_flags=flags)
        else:
            log_shadow_action(session, contact_id, "ghl_finalize_skipped", {
                "reason": "ghl_id_not_found" if not ghl_id else "field_resolution_failed",
            })

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
        resolved_fields = _resolve_to_field_ids(ghl, field_updates)
        ghl_id = _resolve_ghl_contact_id(ghl, contact_id)
        if resolved_fields and ghl_id:
            ghl.update_contact_fields(ghl_id, resolved_fields, mode_flags=flags)
        else:
            log_shadow_action(session, contact_id, "ghl_close_skipped", {
                "reason": "ghl_id_not_found" if not ghl_id else "field_resolution_failed",
            })

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


# ── Missed-webhook recovery via Synthflow call fetch ─────────────────────────

class WebhookRecoveryError(Exception):
    pass


def recover_missed_webhook(
    session: Session,
    contact_id: str,
    synthflow_call_id: str,
    operator_id: str,
    settings: Settings,
) -> dict[str, Any]:
    """
    Fetch a call from the Synthflow API by call_id and replay the full
    process_call_event pipeline, exactly as if the webhook had been delivered.

    Idempotent: if call_events already has this call_id the job is still
    scheduled; process_call_event will skip the DB insert via dedupe_key.
    """
    from app.adapters.synthflow import SynthflowClient, SynthflowError
    from app.worker.scheduler import enqueue_now

    # Idempotency guard — abort if a process_call_event job already exists for
    # this call_id and is still active, to avoid double-processing.
    existing_job = session.execute(
        text(
            "SELECT id FROM scheduled_jobs"
            " WHERE job_type = 'process_call_event'"
            "   AND entity_id = :call_id"
            "   AND status IN ('pending','claimed','running')"
            " LIMIT 1"
        ),
        {"call_id": synthflow_call_id},
    ).fetchone()
    if existing_job:
        raise StaleLeadConflict(
            f"A process_call_event job for call {synthflow_call_id} is already active"
        )

    # Fetch call data from Synthflow
    try:
        sf = SynthflowClient(settings=settings)
        call_data = sf.get_call(synthflow_call_id)
    except SynthflowError as exc:
        raise WebhookRecoveryError(f"Synthflow fetch failed: {exc}") from exc

    call_status = (call_data.get("status") or "").lower()
    if not call_status:
        raise WebhookRecoveryError(
            f"Synthflow returned no status for call {synthflow_call_id}"
        )

    # Build a normalized payload that process_call_event can consume.
    # Mirror the key normalizations from normalize_synthflow_payload() in webhooks.py.
    agent_raw = (call_data.get("Agent") or call_data.get("agent") or "").lower()
    if "cold" in agent_raw:
        campaign_name = "Cold Lead"
    elif "inbound" in agent_raw:
        campaign_name = "Inbound"
    else:
        campaign_name = "New Lead"

    normalized: dict[str, Any] = {
        **call_data,
        "call_id":       synthflow_call_id,
        "contact_id":    contact_id,
        "campaign_name": campaign_name,
        "direction":     call_data.get("direction", "outbound"),
        "duration_seconds": call_data.get("duration_seconds") or call_data.get("duration"),
        "source":        "manual_webhook_recovery",
    }

    # Schedule process_call_event — the worker runs the full pipeline
    job = schedule_job(
        session=session,
        job_type="process_call_event",
        entity_type="call",
        entity_id=synthflow_call_id,
        run_at=datetime.now(timezone.utc),
        payload=normalized,
    )

    audit_id = _write_audit(session, contact_id, operator_id, "manual_webhook_recovery", {
        "synthflow_call_id": synthflow_call_id,
        "call_status":       call_status,
        "campaign_name":     campaign_name,
    })
    logger.info(
        "webhook_recovery | contact_id=%s call_id=%s status=%s job_id=%s",
        contact_id, synthflow_call_id, call_status, job.id if hasattr(job, "id") else job,
    )
    return {
        "action":            "recovery_scheduled",
        "synthflow_call_id": synthflow_call_id,
        "call_status":       call_status,
        "campaign_name":     campaign_name,
        "audit_log_id":      audit_id,
    }
