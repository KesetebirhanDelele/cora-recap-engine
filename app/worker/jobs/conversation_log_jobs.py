"""
GHL Conversations call-log job — spec/19, spec/20.

write_conversation_log — logs a completed call's duration/status into the
contact's GHL Conversations tab via the Marketplace OAuth app
(app/adapters/ghl_conversations.py). Completely separate write path from
crm_jobs.py's Private-Integration-based writes (spec/16) — different auth
mechanism, different shadow gate (GHL_WRITE_CONVERSATION_LOG).

Runs on the `callbacks` queue, same isolation rationale as create_crm_task:
keep GHL write traffic off the default/ai queues.

Does NOT yet attach a recording or transcript — see spec/20 §7. Logs call
duration/status/to/from only.

Skip conditions (not errors, complete_job cleanly):
  - OAuth app not installed on this location yet (no stored token)
  - Dedupe: a 'created' ghl_conversation_log_events row already exists

Failure conditions (create_exception, fail_job, non-fatal to call processing):
  - Missing call_event_id / CallEvent not found
  - GHL contact could not be resolved (no phone match)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db import get_sync_session
from app.models.call_event import CallEvent
from app.models.ghl_conversation_log_event import GhlConversationLogEvent
from app.worker.claim import (
    claim_job,
    complete_job,
    fail_job,
    get_worker_id,
    mark_running,
    release_job_to_pending,
)
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)


def _looks_like_phone(s: str) -> bool:
    """Same helper as crm_jobs.py — inbound calls derive contact_id from a phone string."""
    stripped = s.replace(" ", "").replace("-", "").replace("+", "")
    return bool(stripped) and stripped.isdigit()


def write_conversation_log(job_id: str) -> None:
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("write_conversation_log: job already claimed | job_id=%s", job_id)
            return

        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info("write_conversation_log: system paused — releasing | job_id=%s", job_id)
            release_job_to_pending(session, job)
            session.commit()
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_event_id = payload.get("call_event_id", "")
        call_id = payload.get("call_id", "")
        contact_id = payload.get("contact_id", "")

        try:
            logger.info("write_conversation_log | job_id=%s call_event_id=%s", job_id, call_event_id)

            if not call_event_id:
                raise ValueError(f"Missing call_event_id in job payload | job_id={job_id}")

            call_event = session.get(CallEvent, call_event_id)
            if call_event is None:
                raise ValueError(f"CallEvent not found | call_event_id={call_event_id}")

            # Dedupe: only one successful log entry per call event
            existing = session.scalars(
                select(GhlConversationLogEvent).where(
                    GhlConversationLogEvent.call_event_id == call_event_id,
                    GhlConversationLogEvent.status == "created",
                )
            ).first()
            if existing:
                logger.info(
                    "write_conversation_log: log already exists, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            effective_contact_id = contact_id or call_event.contact_id or ""

            # ── Resolve the real GHL contact — MUST use the phone on file for
            # call.to (spec/20 §7: a well-formatted but non-matching number is
            # rejected). Mirrors crm_jobs.py's create_crm_task resolution
            # pattern — no shared helper exists for this yet (see spec/20 §1).
            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
            resolved_contact: dict = {}

            if effective_contact_id and not _looks_like_phone(effective_contact_id):
                try:
                    fetched = ghl.get_contact(effective_contact_id)
                    resolved_contact = fetched.get("contact", fetched)
                except Exception as read_exc:
                    logger.warning(
                        "write_conversation_log: GHL contact fetch failed | contact_id=%s: %s",
                        effective_contact_id, read_exc,
                    )
            else:
                raw = call_event.raw_payload_json or {}
                phone_to_search = (
                    raw.get("phone_number_to") or raw.get("phone_number") or raw.get("phone")
                    or (effective_contact_id if effective_contact_id else None)
                )
                if phone_to_search:
                    try:
                        found = ghl.search_contact_by_phone(phone_to_search)
                        if found:
                            resolved_contact = found
                    except Exception as read_exc:
                        logger.warning(
                            "write_conversation_log: GHL phone search failed | phone=<redacted>: %s",
                            read_exc,
                        )

            resolved_contact_id = resolved_contact.get("id") or (
                effective_contact_id if effective_contact_id and not _looks_like_phone(effective_contact_id) else None
            )
            contact_phone = resolved_contact.get("phone")

            if not resolved_contact_id or not contact_phone:
                raise ValueError(
                    f"Could not resolve a GHL contact with a phone on file | "
                    f"call_event_id={call_event_id} effective_contact_id={effective_contact_id}"
                )

            # ── OAuth token for this location — skip cleanly if not installed ──
            from app.services.ghl_oauth import get_valid_access_token

            location_id = settings.ghl_oauth_effective_target_location_id
            access_token = (
                get_valid_access_token(session, location_id, settings) if location_id else None
            )
            if access_token is None:
                logger.info(
                    "write_conversation_log: OAuth app not installed for this location — "
                    "skipping cleanly | location_id=%s call_event_id=%s",
                    location_id, call_event_id,
                )
                complete_job(session, job)
                return

            # ── Write ────────────────────────────────────────────────────────
            from app.adapters.ghl_conversations import GhlConversationsClient

            ghl_conv = GhlConversationsClient(settings=settings)
            result = ghl_conv.write_outbound_call(
                access_token,
                contact_id=resolved_contact_id,
                to_phone=contact_phone,
                from_phone=call_event.agent_phone_number or "",
                call_duration_seconds=call_event.duration_seconds or 0,
                call_status="completed",
            )

            log_row = GhlConversationLogEvent(
                id=str(uuid.uuid4()),
                call_event_id=call_event_id,
                ghl_message_id=result.get("messageId") if not result.get("shadow") else None,
                status="created",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(log_row)

            logger.info(
                "write_conversation_log: done | call_event_id=%s shadow=%s",
                call_event_id, result.get("shadow", False),
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "write_conversation_log: error | job_id=%s call_event_id=%s: %s",
                job_id, call_event_id, exc,
            )
            create_exception(
                session,
                type="conversation_log_failed",
                severity="warning",
                context={"call_id": call_id, "call_event_id": call_event_id, "job_id": job_id, "error": str(exc)},
                entity_type="call",
                entity_id=call_id or call_event_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise
