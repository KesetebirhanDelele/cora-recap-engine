"""
GHL InternalComment job — transcript + recording link into Conversations.

write_internal_comment_note — logs a completed call's transcript and
recording URL into the contact's GHL Conversations tab as a staff-only
InternalComment, via app/adapters/ghl_internal_comment.py. Completely
separate write path from both crm_jobs.py (spec/16, Private Integration
fields/tasks) and conversation_log_jobs.py (spec/19/20, OAuth Call-type) —
different auth mechanism (a second Private Integration token), different
shadow gate (GHL_WRITE_INTERNAL_COMMENT).

Runs on the `callbacks` queue, same isolation rationale as the other GHL
write jobs: keep GHL write traffic off the default/ai queues.

Skip conditions (not errors, complete_job cleanly):
  - Dedupe: a 'created' ghl_internal_comment_log_events row already exists
  - CallEvent has no transcript (nothing to write — voicemail/failed calls
    routinely have none, this is expected, not an error)

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
from app.models.ghl_internal_comment_log_event import GhlInternalCommentLogEvent
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
    """Same helper as crm_jobs.py / conversation_log_jobs.py — inbound calls
    derive contact_id from a phone string."""
    stripped = s.replace(" ", "").replace("-", "").replace("+", "")
    return bool(stripped) and stripped.isdigit()


def write_internal_comment_note(job_id: str) -> None:
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("write_internal_comment_note: job already claimed | job_id=%s", job_id)
            return

        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info(
                "write_internal_comment_note: system paused — releasing | job_id=%s", job_id
            )
            release_job_to_pending(session, job)
            session.commit()
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_event_id = payload.get("call_event_id", "")
        call_id = payload.get("call_id", "")
        contact_id = payload.get("contact_id", "")

        try:
            logger.info(
                "write_internal_comment_note | job_id=%s call_event_id=%s", job_id, call_event_id
            )

            if not call_event_id:
                raise ValueError(f"Missing call_event_id in job payload | job_id={job_id}")

            call_event = session.get(CallEvent, call_event_id)
            if call_event is None:
                raise ValueError(f"CallEvent not found | call_event_id={call_event_id}")

            # Dedupe: only one successful note per call event
            existing = session.scalars(
                select(GhlInternalCommentLogEvent).where(
                    GhlInternalCommentLogEvent.call_event_id == call_event_id,
                    GhlInternalCommentLogEvent.status == "created",
                )
            ).first()
            if existing:
                logger.info(
                    "write_internal_comment_note: note already exists, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            transcript = (call_event.transcript or "").strip()
            if not transcript:
                logger.info(
                    "write_internal_comment_note: no transcript — skipping cleanly | "
                    "call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            effective_contact_id = contact_id or call_event.contact_id or ""

            # ── Resolve the real GHL contact (mirrors conversation_log_jobs.py) ──
            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
            resolved_contact: dict = {}
            resolution_error: str | None = None

            if effective_contact_id and not _looks_like_phone(effective_contact_id):
                try:
                    fetched = ghl.get_contact(effective_contact_id)
                    resolved_contact = fetched.get("contact", fetched)
                except Exception as read_exc:
                    resolution_error = f"contact fetch failed: {read_exc}"
                    logger.warning(
                        "write_internal_comment_note: GHL contact fetch failed | contact_id=%s: %s",
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
                        resolution_error = f"phone search failed: {read_exc}"
                        logger.warning(
                            "write_internal_comment_note: GHL phone search failed | phone=<redacted>: %s",
                            read_exc,
                        )

            resolved_contact_id = resolved_contact.get("id") or (
                effective_contact_id if effective_contact_id and not _looks_like_phone(effective_contact_id) else None
            )

            if not resolved_contact_id:
                detail = f" | resolution_error={resolution_error}" if resolution_error else ""
                raise ValueError(
                    f"Could not resolve a GHL contact | "
                    f"call_event_id={call_event_id} effective_contact_id={effective_contact_id}{detail}"
                )

            # ── Write ────────────────────────────────────────────────────────
            from app.adapters.ghl_internal_comment import GhlInternalCommentClient

            note_client = GhlInternalCommentClient(settings=settings)
            result = note_client.write_call_note(
                contact_id=resolved_contact_id,
                call_id=call_id or call_event.call_id,
                transcript=transcript,
                recording_url=call_event.recording_url,
            )

            log_row = GhlInternalCommentLogEvent(
                id=str(uuid.uuid4()),
                call_event_id=call_event_id,
                ghl_message_id=result.get("messageId") if not result.get("shadow") else None,
                status="created",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(log_row)

            logger.info(
                "write_internal_comment_note: done | call_event_id=%s shadow=%s",
                call_event_id, result.get("shadow", False),
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "write_internal_comment_note: error | job_id=%s call_event_id=%s: %s",
                job_id, call_event_id, exc,
            )
            create_exception(
                session,
                type="internal_comment_note_failed",
                severity="warning",
                context={"call_id": call_id, "call_event_id": call_event_id, "job_id": job_id, "error": str(exc)},
                entity_type="call",
                entity_id=call_id or call_event_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise
