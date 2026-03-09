"""
Voicemail tier advancement job — runs on the `default` RQ queue.

Implements the canonical voicemail tier model: None → 0 → 1 → 2 → 3.
Campaign-specific policy (delays, actions, finalization) is applied here
using the settings-configured tier delays.

Phase 6: claim/fail/exception wiring complete. Tier advancement logic stubbed.
Phase 7: Synthflow callback scheduling wired into this job.

Stop condition (from autonomous execution contract):
  Do not advance tier without valid campaign_name and current tier state.
  Invalid tier transitions must create an exception record.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)

# Canonical tier progression
_TIER_SEQUENCE = [None, "0", "1", "2", "3"]
_TERMINAL_TIER = "3"


def process_voicemail_tier(job_id: str) -> None:
    """
    Advance the voicemail tier for a contact and schedule the next step.

    1. Claim the job
    2. Load current ai_campaign_value from lead_state
    3. Determine next tier using canonical model
    4. Apply campaign-specific delay and actions
    5. Update lead_state tier
    6. Schedule Synthflow callback if not terminal (Phase 7)
    7. Schedule next tier job at the campaign delay
    8. If tier == 3 (terminal): execute finalization writes (shadow-gated)
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("process_voicemail_tier: job already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_id = payload.get("call_id", "")
        contact_id = payload.get("contact_id", "")

        try:
            logger.info(
                "process_voicemail_tier | job_id=%s call_id=%s contact_id=%s",
                job_id, call_id, contact_id,
            )

            # Load lead state
            from sqlalchemy import select

            from app.models.lead_state import LeadState

            lead = None
            if contact_id:
                lead = session.scalars(
                    select(LeadState).where(LeadState.contact_id == contact_id)
                ).first()

            if lead is None:
                raise ValueError(
                    f"Cannot advance voicemail tier — lead_state not found | "
                    f"contact_id={contact_id}"
                )

            current_tier = lead.ai_campaign_value  # None | '0' | '1' | '2' | '3'
            campaign_name = lead.campaign_name or ""
            next_tier = _get_next_tier(current_tier)

            if next_tier is None:
                raise ValueError(
                    f"Invalid tier transition from {current_tier!r} | "
                    f"contact_id={contact_id}"
                )

            # Get campaign-specific policy (delay, callback flag, terminal flag)
            from app.services.tier_policy import get_tier_policy, has_pending_callback

            policy = get_tier_policy(campaign_name, current_tier, settings)

            logger.info(
                "voicemail tier advance | contact_id=%s %r → %r campaign=%s "
                "delay_min=%d schedule_callback=%s terminal=%s",
                contact_id, current_tier, next_tier, campaign_name,
                policy.delay_minutes, policy.schedule_synthflow_callback,
                policy.is_terminal,
            )

            # Advance tier in Postgres (optimistic concurrency)
            _advance_tier(session, lead, next_tier)

            # Terminal tier: finalize campaign (sets AI Campaign=No in GHL)
            if policy.is_terminal:
                _finalize_campaign(session, lead, settings)

            # Non-terminal tier: schedule Synthflow callback + next tier check
            elif policy.schedule_synthflow_callback:
                phone = lead.normalized_phone or ""
                if not phone:
                    raise ValueError(
                        f"Cannot schedule callback — no phone for contact_id={contact_id}"
                    )

                # Duplicate prevention: only schedule if no pending callback exists
                if has_pending_callback(session, contact_id):
                    logger.warning(
                        "voicemail_jobs: duplicate callback skipped | contact_id=%s",
                        contact_id,
                    )
                else:
                    _schedule_synthflow_callback(session, phone, contact_id, policy, settings)

            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "process_voicemail_tier: error | job_id=%s call_id=%s: %s",
                job_id, call_id, exc,
            )
            create_exception(
                session,
                type="voicemail_tier_failed",
                severity="critical",
                context={"call_id": call_id, "contact_id": contact_id,
                         "job_id": job_id, "error": str(exc)},
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


def _get_next_tier(current_tier: str | None) -> str | None:
    """
    Return the next tier in the canonical sequence, or None if invalid.

    Canonical: None → '0' → '1' → '2' → '3'
    '3' is terminal — no further advancement.
    """
    if current_tier == _TERMINAL_TIER:
        return None  # already at terminal
    try:
        idx = _TIER_SEQUENCE.index(current_tier)
        return _TIER_SEQUENCE[idx + 1]
    except (ValueError, IndexError):
        return None


def _advance_tier(session, lead, next_tier: str) -> None:
    """Update lead_state ai_campaign_value to next_tier using optimistic concurrency."""
    from datetime import datetime, timezone

    from sqlalchemy import update

    from app.models.lead_state import LeadState

    now = datetime.now(tz=timezone.utc)
    result = session.execute(
        update(LeadState)
        .where(
            LeadState.id == lead.id,
            LeadState.version == lead.version,
        )
        .values(
            ai_campaign_value=next_tier,
            version=lead.version + 1,
            updated_at=now,
        )
    )
    session.flush()
    if result.rowcount == 0:
        raise RuntimeError(
            f"Tier advance version conflict | lead_id={lead.id} "
            f"expected_version={lead.version}"
        )
    session.refresh(lead)


def _finalize_campaign(session, lead, settings) -> None:
    """
    Execute finalization writes when tier reaches 3.

    Sets AI Campaign = 'No' in GHL (shadow-gated).
    This terminates AI campaign activity for the contact.
    """
    from app.adapters.ghl import GHLClient

    logger.info(
        "voicemail finalization | contact_id=%s tier=3 shadow=%s",
        lead.contact_id, not settings.ghl_writes_enabled,
    )
    ghl = GHLClient(settings=settings)
    ai_campaign_field = settings.ghl_field_ai_campaign or "AI Campaign"
    ghl.update_contact_fields(
        contact_id=lead.contact_id,
        field_updates={ai_campaign_field: "No"},
    )


def _schedule_synthflow_callback(session, phone, contact_id, policy, settings) -> None:
    """
    Schedule a Synthflow callback and create a durable scheduled_job record.

    The callback is scheduled with a delay per the campaign policy.
    A scheduled_jobs record is created first (Postgres-authoritative).
    """
    from datetime import datetime, timedelta, timezone

    from app.adapters.synthflow import SynthflowClient
    from app.worker.scheduler import schedule_job

    delay = timedelta(minutes=policy.delay_minutes)
    run_at = datetime.now(tz=timezone.utc) + delay
    scheduled_time = run_at if policy.delay_minutes > 0 else None

    # Create durable job record
    job = schedule_job(
        session=session,
        job_type="synthflow_callback",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload={
            "phone": phone,
            "contact_id": contact_id,
            "campaign": policy.campaign_name,
            "tier": policy.next_tier,
            "delay_minutes": policy.delay_minutes,
        },
    )

    # Fire the Synthflow API call (shadow-gated via validate_for_synthflow)
    try:
        client = SynthflowClient(settings=settings)
        result = client.schedule_callback(
            phone=phone,
            scheduled_time=scheduled_time,
            metadata={
                "contact_id": contact_id,
                "campaign": policy.campaign_name,
                "tier": policy.next_tier,
                "scheduled_job_id": job.id,
            },
        )
        logger.info(
            "Synthflow callback scheduled | contact_id=%s tier=%s delay_min=%d "
            "synthflow_call_id=%s",
            contact_id, policy.next_tier, policy.delay_minutes,
            result.get("id", "unknown"),
        )
    except Exception as exc:
        logger.exception(
            "_schedule_synthflow_callback: API call failed | contact_id=%s: %s",
            contact_id, exc,
        )
        # Job record exists in Postgres; worker recovery loop can retry
        raise
