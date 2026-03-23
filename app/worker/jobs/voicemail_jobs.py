"""
Voicemail tier advancement job — runs on the `default` RQ queue.

Implements the canonical voicemail tier model: None → 0 → 1 → 2 → 3.
Campaign-specific policy (delays, actions, finalization) is applied here
using the settings-configured tier delays.

Phase 6: claim/fail/exception wiring complete. Tier advancement logic stubbed.
Phase 7: Synthflow callback scheduling wired into this job.
Phase 8: Intent detection — when a transcript is present, detect_intent() is
  called before tier logic. A detected intent triggers handle_intent() and
  short-circuits the tier loop (complete + return).

Stop condition (from autonomous execution contract):
  Do not advance tier without valid campaign_name and current tier state.
  Invalid tier transitions must create an exception record.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

# Canonical tier progression
_TIER_SEQUENCE = [None, "0", "1", "2", "3"]
_TERMINAL_TIER = "3"

# Maps tier → 1-based attempt number (for observability in payload_json)
_TIER_TO_ATTEMPT: dict[str | None, int] = {None: 1, "0": 2, "1": 3}


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
        # Derive contact_id from any available phone field when absent.
        # Synthflow webhooks may not include a GHL contact_id; phone is the fallback key.
        contact_id = (
            payload.get("contact_id")
            or payload.get("phone")
            or payload.get("phone_number")
            or ""
        )
        payload_campaign_name = payload.get("campaign_name") or "New Lead"
        payload_lead_name = payload.get("lead_name", "") or ""

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
                if not contact_id:
                    raise ValueError(
                        f"Cannot create lead_state — contact_id and phone are both empty | "
                        f"job_id={job_id}"
                    )
                logger.info(
                    "process_voicemail_tier: no lead_state found, creating | "
                    "contact_id=%s job_id=%s",
                    contact_id, job_id,
                )
                # Resolve the phone to store in normalized_phone.
                # When contact_id was derived from a phone field (no GHL id yet),
                # the phone fields in the payload hold the dialable number.
                derived_phone = (
                    payload.get("phone_number")
                    or payload.get("phone")
                    or contact_id
                ) or None
                now = datetime.now(tz=timezone.utc)
                lead = LeadState(
                    id=str(uuid.uuid4()),
                    contact_id=contact_id,
                    normalized_phone=derived_phone,
                    campaign_name=payload_campaign_name or None,
                    ai_campaign_value=None,
                    version=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(lead)
                session.flush()

            current_tier = lead.ai_campaign_value  # None | '0' | '1' | '2' | '3'
            # campaign_name: prefer the value stored on the row (set by GHL sync),
            # fall back to what was forwarded in the job payload (useful for testing
            # and for contacts whose lead_state was auto-created this invocation).
            campaign_name = lead.campaign_name or payload_campaign_name

            # ── Intent detection ──────────────────────────────────────────────
            # When the call produced a transcript (e.g. person left a voicemail
            # message or spoke briefly before the call ended), check for an
            # explicit intent.  A detected intent overrides tier logic entirely.
            call_event_id = payload.get("call_event_id")
            transcript = _load_transcript(session, call_event_id)
            if transcript.strip():
                from app.core.intent_detection import detect_intent
                from app.core.intent_actions import handle_intent

                intent_result = detect_intent(transcript)
                if intent_result is not None:
                    logger.info(
                        "process_voicemail_tier: intent detected, overriding tier logic | "
                        "contact_id=%s intent=%s confidence=%.2f",
                        contact_id, intent_result["intent"],
                        intent_result.get("confidence", 0.0),
                    )
                    handle_intent(
                        session=session,
                        intent_result=intent_result,
                        contact_id=contact_id,
                        phone=lead.normalized_phone or "",
                        current_job_id=job.id,
                        settings=settings,
                    )
                    complete_job(session, job)
                    return

            # Already at terminal tier — nothing to do, complete cleanly.
            if current_tier == _TERMINAL_TIER:
                logger.info(
                    "process_voicemail_tier: already at final tier, no retries | "
                    "contact_id=%s tier=%s",
                    contact_id, current_tier,
                )
                complete_job(session, job)
                return

            next_tier = _get_next_tier(current_tier)

            if next_tier is None:
                # Unknown tier value — this is a real data problem, not a normal stop.
                raise ValueError(
                    f"Invalid tier transition from {current_tier!r} | "
                    f"contact_id={contact_id}"
                )

            # Get campaign-specific policy (delay, callback flag, terminal flag)
            from app.services.tier_policy import get_tier_policy

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

            # Non-terminal tier: schedule retry outbound call
            elif policy.schedule_synthflow_callback:
                phone = lead.normalized_phone or ""
                if not phone:
                    raise ValueError(
                        f"Cannot schedule retry call — no phone for contact_id={contact_id}"
                    )

                _schedule_retry_outbound_call(
                    session, contact_id, phone, call_id, current_tier, campaign_name, settings,
                    lead_name=payload_lead_name,
                )

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


def _load_transcript(session, call_event_id: str | None) -> str:
    """
    Load the transcript from the CallEvent row for this voicemail job.

    Returns an empty string when call_event_id is absent, the row is not
    found, or the transcript field is NULL.  Callers check `.strip()` to
    decide whether intent detection is worth running.
    """
    if not call_event_id:
        return ""
    try:
        from app.models.call_event import CallEvent
        event = session.get(CallEvent, call_event_id)
        return (event.transcript or "") if event else ""
    except Exception as exc:  # pragma: no cover
        logger.warning("_load_transcript: failed to load | call_event_id=%s: %s",
                       call_event_id, exc)
        return ""


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


def _make_default_queue(settings):
    """
    Build an RQ Queue for the `default` queue.

    Returns None if Redis is unreachable (job stays pending in Postgres).
    """
    try:
        import redis
        from rq import Queue

        url = (
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )
        ssl_kwargs = {"ssl_cert_reqs": None} if url.startswith("rediss://") else {}
        auth_kwargs: dict = {}
        if settings.redis_username:
            auth_kwargs["username"] = settings.redis_username
        if settings.redis_password:
            auth_kwargs["password"] = settings.redis_password
        conn = redis.from_url(url, **ssl_kwargs, **auth_kwargs)
        return Queue(settings.rq_default_queue, connection=conn)
    except Exception as exc:
        logger.warning("_make_default_queue: Redis unavailable, job will stay pending | %s", exc)
        return None


def _schedule_retry_outbound_call(
    session, contact_id: str, phone: str, call_id: str, current_tier: str | None,
    campaign_name: str, settings, *, lead_name: str = "",
) -> None:
    """
    Schedule a launch_outbound_call retry job after a voicemail is detected.

    Retry delays come from settings, keyed by campaign:
      New Lead → new_vm_tier_none/0/1_delay_minutes; stop when new_vm_tier_2_finalize=True
      Cold Lead → cold_vm_tier_none/0/1_delay_minutes; stop when cold_vm_tier_2_finalizes=True

    vm_retry_attempt (1-based) is stored in the job payload for observability.
    Tier '2' with finalize=True and tier '3' produce no retry job.

    phone is forwarded as phone_number so launch_outbound_call_job can dial the contact.
    lead_name is forwarded so Synthflow receives the contact's name on the retry call.
    """
    campaign_lower = (campaign_name or "").strip().lower()

    if campaign_lower == "cold lead":
        delay_map: dict[str | None, int | None] = {
            None: settings.cold_vm_tier_none_delay_minutes,
            "0":  settings.cold_vm_tier_0_delay_minutes,
            "1":  settings.cold_vm_tier_1_delay_minutes,
        }
        stop_at_tier_2 = bool(settings.cold_vm_tier_2_finalizes)
    else:
        # New Lead (or unknown campaign — default to new_vm_tier_* with a warning)
        if campaign_lower not in ("new lead", "new_lead"):
            logger.warning(
                "_schedule_retry_outbound_call: unknown campaign %r, "
                "falling back to new_vm_tier_* settings | contact_id=%s",
                campaign_name, contact_id,
            )
        delay_map = {
            None: settings.new_vm_tier_none_delay_minutes,
            "0":  settings.new_vm_tier_0_delay_minutes,
            "1":  settings.new_vm_tier_1_delay_minutes,
        }
        stop_at_tier_2 = settings.new_vm_tier_2_finalize is True

    # Tier '2' with finalize flag → stop
    if current_tier == "2" and stop_at_tier_2:
        logger.info(
            "_schedule_retry_outbound_call: tier '2' + finalize=true, "
            "no retry | contact_id=%s campaign=%s",
            contact_id, campaign_name,
        )
        return

    delay_minutes = delay_map.get(current_tier)
    if delay_minutes is None:
        logger.info(
            "_schedule_retry_outbound_call: no delay configured for tier %r, "
            "skipping | contact_id=%s campaign=%s",
            current_tier, contact_id, campaign_name,
        )
        return

    attempt_number = _TIER_TO_ATTEMPT.get(current_tier, 0)

    from datetime import timedelta

    from app.worker.jobs.outbound_jobs import launch_outbound_call_job

    run_at = datetime.now(tz=timezone.utc) + timedelta(minutes=delay_minutes)
    default_queue = _make_default_queue(settings)

    schedule_job(
        session=session,
        job_type="launch_outbound_call",
        entity_type="lead",
        entity_id=contact_id,
        run_at=run_at,
        payload={
            "contact_id": contact_id,
            "phone_number": phone,
            "lead_name": lead_name,
            "call_id": call_id,
            "vm_retry_attempt": attempt_number,
            "delay_minutes": delay_minutes,
            "campaign_name": campaign_name,
        },
        rq_queue=default_queue,
        rq_job_func=launch_outbound_call_job if default_queue is not None else None,
    )
    logger.info(
        "voicemail retry scheduled | contact_id=%s tier=%r delay=%d run_at=%s",
        contact_id, current_tier, delay_minutes, run_at.isoformat(),
    )


