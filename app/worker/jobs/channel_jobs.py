"""
Channel delivery jobs — SMS and email.

send_sms_job:
  Scheduled after a missed call/voicemail.
  Generates AI-personalised content (falls back to template).
  Stores result in outbound_messages.

send_email_job:
  Scheduled 1 day after the second call attempt.
  Same AI-generation + fallback pattern as SMS.

SMS/email replies are handled entirely within GHL automations — no reply
signals come back to this system. There is no reply-suppression gate here.

Both jobs:
  1. Check campaign active window in caller's local timezone (live mode only).
     If outside window: cancel current job, reschedule at next window-open time.

Payload fields (both jobs):
  contact_id      — GHL contact identifier
  campaign_name   — used for active-window policy selection
  attempt_number  — 1-based attempt count (for context)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running, release_job_to_pending
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)


def _check_active_window(session, job, contact_id: str, campaign_name: str, settings) -> bool:
    """
    Check whether the campaign is within its active window in the caller's timezone.

    Returns True if the job should proceed.
    Returns False (and handles cancel + reschedule) if the job was deferred.
    Enforced in both live and shadow mode — shadow mode simulates the same
    window constraints so scheduling behaviour matches production exactly.
    """
    from app.core.campaign_schedule import (
        get_contact_timezone,
        is_campaign_active,
        next_active_window_start,
    )
    from app.worker.claim import cancel_job
    from app.worker.scheduler import schedule_job

    now = datetime.now(tz=timezone.utc)
    contact_tz = get_contact_timezone(session, contact_id, settings)
    if is_campaign_active(campaign_name, now, settings, contact_tz, session):
        return True

    next_open = next_active_window_start(campaign_name, now, settings, contact_tz, session)
    logger.info(
        "%s: outside active window — deferring | "
        "contact_id=%s campaign=%s contact_tz=%s rescheduled_for=%s",
        job.job_type, contact_id, campaign_name, contact_tz, next_open.isoformat(),
    )
    cancel_job(session, job.id)
    schedule_job(
        session=session,
        job_type=job.job_type,
        entity_type=job.entity_type,
        entity_id=job.entity_id,
        run_at=next_open,
        payload=job.payload_json or {},
    )
    return False


def send_sms_job(job_id: str) -> None:
    """
    Worker job: generate and record an outbound SMS.

    Defers to next active window if outside calling hours.
    Skips silently if the contact has already replied.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_sms_job: already claimed | job_id=%s", job_id)
            return

        # ── System pause check ────────────────────────────────────────────────
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info("send_sms_job: system paused — releasing | job_id=%s", job_id)
            release_job_to_pending(session, job)
            session.commit()
            return

        # ── Outbound campaign pause check ─────────────────────────────────────
        if flags.outbound_campaigns_paused:
            _campaign = ((job.payload_json or {}).get("campaign_name") or "").strip().lower()
            if _campaign in ("new lead", "cold lead"):
                logger.info(
                    "send_sms_job: outbound campaigns paused — releasing | "
                    "campaign=%r job_id=%s", _campaign, job_id,
                )
                release_job_to_pending(session, job, defer_seconds=60)
                session.commit()
                return

        # Load payload before mark_running so the window check can cancel
        # the job while it is still in 'claimed' status.
        payload = job.payload_json or {}
        contact_id = payload.get("contact_id", "")
        campaign_name = payload.get("campaign_name", "")

        # ── Campaign active-window check ──────────────────────────────────────
        if not _check_active_window(session, job, contact_id, campaign_name, settings):
            return

        mark_running(session, job)

        try:
            from app.core.ai_message_generator import generate_vm_followup
            from app.core.conversation_context import get_conversation_context
            from app.models.outbound_message import OutboundMessage

            attempt_number = int(payload.get("attempt_number") or 1)
            context = get_conversation_context(session, contact_id, attempt_number=attempt_number)
            result = generate_vm_followup(context, settings, session)

            now = datetime.now(tz=timezone.utc)

            # ── Shadow mode: write content to outbound_messages (status='shadow')
            #    and log full payload to shadow_actions — real send skipped.
            if flags.shadow_mode_enabled:
                from app.worker.shadow import log_shadow_action
                outbound = OutboundMessage(
                    id=str(uuid.uuid4()),
                    contact_id=contact_id,
                    channel="sms",
                    body=result.sms_text,
                    status="shadow",
                    created_at=now,
                )
                session.add(outbound)
                session.flush()
                log_shadow_action(
                    session,
                    contact_id=contact_id,
                    action_type="sms",
                    payload={
                        "contact_id": contact_id,
                        "attempt_number": attempt_number,
                        "campaign_name": campaign_name,
                        "message_body": result.sms_text,
                    },
                )
                _schedule_ghl_vm_update(
                    session=session,
                    contact_id=contact_id,
                    channel="sms",
                    message_body=result.sms_text,
                    message_subject="",
                    campaign_name=campaign_name,
                )
                logger.info(
                    "send_sms_job: SMS generated (shadow) | contact_id=%s length=%d attempt=%d job_id=%s",
                    contact_id, len(result.sms_text), attempt_number, job_id,
                )
                complete_job(session, job)
                return

            outbound = OutboundMessage(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                channel="sms",
                body=result.sms_text,
                status="pending",
                created_at=now,
            )
            session.add(outbound)
            session.flush()

            # ── Path 2: GHL field update after VM-tier SMS ────────────────
            _schedule_ghl_vm_update(
                session=session,
                contact_id=contact_id,
                channel="sms",
                message_body=result.sms_text,
                message_subject="",
                campaign_name=campaign_name,
            )

            logger.info(
                "send_sms_job: SMS recorded | contact_id=%s length=%d attempt=%d job_id=%s",
                contact_id, len(result.sms_text), attempt_number, job_id,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "send_sms_job: error | contact_id=%s job_id=%s: %s",
                contact_id, job_id, exc,
            )
            create_exception(
                session,
                type="send_sms_failed",
                severity="warning",
                context={"contact_id": contact_id, "job_id": job_id, "error": str(exc)},
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise


def send_email_job(job_id: str) -> None:
    """
    Worker job: generate and record an outbound email.

    Defers to next active window if outside calling hours.
    Skips silently if the contact has already replied.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_email_job: already claimed | job_id=%s", job_id)
            return

        # ── System pause check ────────────────────────────────────────────────
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info("send_email_job: system paused — releasing | job_id=%s", job_id)
            release_job_to_pending(session, job)
            session.commit()
            return

        # ── Outbound campaign pause check ─────────────────────────────────────
        if flags.outbound_campaigns_paused:
            _campaign = ((job.payload_json or {}).get("campaign_name") or "").strip().lower()
            if _campaign in ("new lead", "cold lead"):
                logger.info(
                    "send_email_job: outbound campaigns paused — releasing | "
                    "campaign=%r job_id=%s", _campaign, job_id,
                )
                release_job_to_pending(session, job, defer_seconds=60)
                session.commit()
                return

        # Load payload before mark_running so the window check can cancel
        # the job while it is still in 'claimed' status.
        payload = job.payload_json or {}
        contact_id = payload.get("contact_id", "")
        campaign_name = payload.get("campaign_name", "")

        # ── Campaign active-window check ──────────────────────────────────────
        if not _check_active_window(session, job, contact_id, campaign_name, settings):
            return

        mark_running(session, job)

        try:
            from app.core.ai_message_generator import generate_vm_followup
            from app.core.conversation_context import get_conversation_context
            from app.models.outbound_message import OutboundMessage

            attempt_number = int(payload.get("attempt_number") or 1)
            context = get_conversation_context(session, contact_id, attempt_number=attempt_number)
            result = generate_vm_followup(context, settings, session)

            now = datetime.now(tz=timezone.utc)

            # ── Shadow mode: write content to outbound_messages (status='shadow')
            #    and log full payload to shadow_actions — real send skipped.
            if flags.shadow_mode_enabled:
                from app.worker.shadow import log_shadow_action
                outbound = OutboundMessage(
                    id=str(uuid.uuid4()),
                    contact_id=contact_id,
                    channel="email",
                    subject=result.email_subject,
                    body=result.email_html,
                    status="shadow",
                    created_at=now,
                )
                session.add(outbound)
                session.flush()
                log_shadow_action(
                    session,
                    contact_id=contact_id,
                    action_type="email",
                    payload={
                        "contact_id": contact_id,
                        "attempt_number": attempt_number,
                        "campaign_name": campaign_name,
                        "email_subject": result.email_subject,
                        "message_body": result.email_html,
                    },
                )
                _schedule_ghl_vm_update(
                    session=session,
                    contact_id=contact_id,
                    channel="email",
                    message_body=result.email_html,
                    message_subject=result.email_subject,
                    campaign_name=campaign_name,
                )
                logger.info(
                    "send_email_job: email generated (shadow) | contact_id=%s subject=%r attempt=%d job_id=%s",
                    contact_id, result.email_subject, attempt_number, job_id,
                )
                complete_job(session, job)
                return

            outbound = OutboundMessage(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                channel="email",
                subject=result.email_subject,
                body=result.email_html,
                status="pending",
                created_at=now,
            )
            session.add(outbound)
            session.flush()

            # ── Path 2: GHL field update after VM-tier Email ──────────────
            _schedule_ghl_vm_update(
                session=session,
                contact_id=contact_id,
                channel="email",
                message_body=result.email_html,
                message_subject=result.email_subject,
                campaign_name=campaign_name,
            )

            logger.info(
                "send_email_job: email recorded | contact_id=%s subject=%r attempt=%d job_id=%s",
                contact_id, result.email_subject, attempt_number, job_id,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "send_email_job: error | contact_id=%s job_id=%s: %s",
                contact_id, job_id, exc,
            )
            create_exception(
                session,
                type="send_email_failed",
                severity="warning",
                context={"contact_id": contact_id, "job_id": job_id, "error": str(exc)},
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise


# ── GHL path-2 scheduler ─────────────────────────────────────────────────────

def _schedule_ghl_vm_update(
    session,
    contact_id: str,
    channel: str,
    message_body: str,
    message_subject: str,
    campaign_name: str = "",
) -> None:
    """
    Enqueue update_ghl_after_vm_message immediately after a VM message is generated.
    Non-fatal — failure is logged but must not disrupt the parent SMS/email job.
    """
    try:
        from app.worker.jobs.crm_jobs import update_ghl_after_vm_message
        from app.worker.scheduler import schedule_job

        schedule_job(
            session=session,
            job_type="update_ghl_after_vm_message",
            entity_type="lead",
            entity_id=contact_id,
            run_at=datetime.now(tz=timezone.utc),
            payload={
                "contact_id": contact_id,
                "channel": channel,
                "message_body": message_body,
                "message_subject": message_subject,
                "campaign_name": campaign_name,
            },
            rq_queue=None,
            rq_job_func=update_ghl_after_vm_message,
        )
        logger.info(
            "_schedule_ghl_vm_update: scheduled | contact_id=%s channel=%s",
            contact_id, channel,
        )
    except Exception as exc:
        logger.error(
            "_schedule_ghl_vm_update: failed (non-fatal) | contact_id=%s: %s",
            contact_id, exc,
        )
