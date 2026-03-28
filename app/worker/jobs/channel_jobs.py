"""
Channel delivery jobs — SMS and email.

send_sms_job:
  Scheduled 30 minutes after a missed call/voicemail.
  Generates AI-personalised content (falls back to template).
  Stores result in outbound_messages.
  Suppressed if the contact has already replied.

send_email_job:
  Scheduled 1 day after the second call attempt.
  Same AI-generation + fallback + suppression pattern as SMS.

Both jobs check has_recent_reply() immediately after claiming —
a reply received between scheduling and execution cancels the send.

Payload fields (both jobs):
  contact_id      — GHL contact identifier
  attempt_number  — 1-based attempt count (for context)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)


def send_sms_job(job_id: str) -> None:
    """
    Worker job: generate and record an outbound SMS.

    Skips silently if the contact has already replied.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_sms_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        contact_id = payload.get("contact_id", "")

        try:
            from app.core.reply_detection import has_recent_reply

            if has_recent_reply(session, contact_id):
                logger.info(
                    "send_sms_job: reply detected — suppressing SMS | "
                    "contact_id=%s job_id=%s",
                    contact_id, job_id,
                )
                complete_job(session, job)
                return

            # ── Shadow mode: log and skip AI generation + outbound write ──────
            if settings.shadow_mode_enabled:
                from app.worker.shadow import log_shadow_action
                log_shadow_action(
                    session,
                    contact_id=contact_id,
                    action_type="sms",
                    payload={
                        "contact_id": contact_id,
                        "attempt_number": payload.get("attempt_number"),
                        "campaign_name": payload.get("campaign_name"),
                    },
                )
                complete_job(session, job)
                return

            from app.core.ai_message_generator import generate_sms
            from app.core.conversation_context import get_conversation_context
            from app.models.outbound_message import OutboundMessage

            context = get_conversation_context(session, contact_id)
            sms_body = generate_sms(context, settings)

            now = datetime.now(tz=timezone.utc)
            outbound = OutboundMessage(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                channel="sms",
                body=sms_body,
                status="pending",
                created_at=now,
            )
            session.add(outbound)
            session.flush()

            logger.info(
                "send_sms_job: SMS recorded | contact_id=%s length=%d job_id=%s",
                contact_id, len(sms_body), job_id,
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
            raise


def send_email_job(job_id: str) -> None:
    """
    Worker job: generate and record an outbound email.

    Skips silently if the contact has already replied.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_email_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        contact_id = payload.get("contact_id", "")

        try:
            from app.core.reply_detection import has_recent_reply

            if has_recent_reply(session, contact_id):
                logger.info(
                    "send_email_job: reply detected — suppressing email | "
                    "contact_id=%s job_id=%s",
                    contact_id, job_id,
                )
                complete_job(session, job)
                return

            # ── Shadow mode: log and skip AI generation + outbound write ──────
            if settings.shadow_mode_enabled:
                from app.worker.shadow import log_shadow_action
                log_shadow_action(
                    session,
                    contact_id=contact_id,
                    action_type="email",
                    payload={
                        "contact_id": contact_id,
                        "attempt_number": payload.get("attempt_number"),
                        "campaign_name": payload.get("campaign_name"),
                    },
                )
                complete_job(session, job)
                return

            from app.core.ai_message_generator import generate_email
            from app.core.conversation_context import get_conversation_context
            from app.models.outbound_message import OutboundMessage

            context = get_conversation_context(session, contact_id)
            email = generate_email(context, settings)

            now = datetime.now(tz=timezone.utc)
            outbound = OutboundMessage(
                id=str(uuid.uuid4()),
                contact_id=contact_id,
                channel="email",
                subject=email.subject,
                body=email.body,
                status="pending",
                created_at=now,
            )
            session.add(outbound)
            session.flush()

            logger.info(
                "send_email_job: email recorded | contact_id=%s subject=%r job_id=%s",
                contact_id, email.subject, job_id,
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
            raise
