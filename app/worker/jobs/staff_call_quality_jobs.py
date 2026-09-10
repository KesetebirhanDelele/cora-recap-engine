"""
Staff call quality scan job — periodic discovery + scoring of human
sales-rep/support-staff calls placed through GHL's native dialer (spec/23).

Distinct from Cora's own outbound/inbound calls (CallEvent, Synthflow) —
this covers calls Cora has no other visibility into.

Runs every _SCAN_INTERVAL_SECONDS. Each cycle:
1. Page forward through search_conversations(sort_by="last_message_date",
   sort="asc", start_after_date=<lookback cursor>) — location-wide, using
   ghl_conversations_api_key (see GHLClient.api_key_override), until a page
   comes back short of the page size (caught up to "now") or a safety page
   cap is hit. No last_message_type filter: a call immediately followed by
   a rep's note (common — reps summarize right after hanging up) still
   surfaces, since we're not filtering on what the *most recent* message
   type is. See spec/23 — an earlier version filtered on
   last_message_type="TYPE_CALL" and a single unpaginated page, which
   (confirmed live, never having run in prod — feature is off by default)
   both silently missed note-masked calls AND, independently, walked
   *backward* into a stale ~24-66h-old window instead of "since 24h ago,
   forward to now" (GHL's start_after_date is a pagination cursor, not a
   "since this time" filter, and its default sort is descending).
2. For each matching conversation, walk its messages for TYPE_CALL entries
   not yet in staff_call_quality (dedupe by ghl_message_id — unique
   constraint is the backstop; a pre-check query avoids redundant work).
3. For each new call message:
   a. call_connected = status == "completed" and duration >= threshold
   b. If connected: transcript via GHL's own transcription endpoint first,
      falling back to pulling the recording + OpenAI Whisper (GHL isn't
      generating transcripts on this account as of 2026-08-25 — see
      spec/23 — so the fallback path is expected to be the common one).
   c. Fetch the GHL contact (ghl_api_key — contacts scope) for
      classification signals + support-ticket context.
   d. Classify conversation_type: known signals -> AI transcript fallback.
   e. Score via the matching rubric if connected and type in (sales, support).
   f. Persist one StaffCallQuality row.
4. Reschedule.

Per-item failures are isolated (logged + skipped) so one bad call doesn't
stop the rest of the cycle — same pattern as auto_webhook_recovery_job.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PER_CYCLE_CAP = 20
_LOOKBACK_HOURS = 24
_SCHEDULE_INTERVAL_SECONDS = 900  # 15 minutes
_MIN_CONNECTED_DURATION_SECONDS = 20
_OPERATOR_ID = "staff_call_quality_scan"
_DISCOVERY_PAGE_SIZE = 100  # GHL's documented hard max for /conversations/search
_MAX_DISCOVERY_PAGES = 20  # safety cap — up to 2,000 conversations/cycle; see _discover_conversations


def staff_call_quality_scan_job(job_id: str) -> None:
    """Entrypoint. Follows the same claim/run/complete/reschedule lifecycle as other scan jobs."""
    from app.config import get_settings
    from app.db import get_sync_session
    from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
    from app.worker.exceptions import create_exception

    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        # Default 300s lease is shorter than this job's realistic worst case
        # (discovery fan-out + per-call Whisper transcription fallback — see
        # module docstring). A too-short lease meant recover_expired_claims
        # reset the row mid-run and the cycle retried from scratch forever
        # (spec/29, observed 2026-09-10). Match the self-reschedule cadence.
        job = claim_job(session, job_id=job_id, worker_id=worker_id, lease_seconds=_SCHEDULE_INTERVAL_SECONDS)
        if job is None:
            logger.info("staff_call_quality_scan: could not claim job_id=%s — skipping", job_id)
            return

        mark_running(session, job)
        session.commit()

        try:
            _run_scan_cycle(session, settings)
            complete_job(session, job)
            session.commit()
        except Exception as exc:
            logger.error("staff_call_quality_scan: cycle failed: %s", exc, exc_info=True)
            fail_job(session, job, reason=str(exc))
            create_exception(
                session,
                type="staff_call_quality_scan_failed",
                context={"job_id": job.id, "error": str(exc)},
                severity="warning",
                entity_type="system",
                entity_id="staff_call_quality_scan",
            )
            session.commit()
        finally:
            _reschedule(session)
            session.commit()


def _run_scan_cycle(session: Session, settings: Any) -> None:
    from app.adapters.ghl import GHLClient

    if not getattr(settings, "staff_call_quality_scan_enabled", False):
        logger.debug("staff_call_quality_scan: disabled (STAFF_CALL_QUALITY_SCAN_ENABLED=false) — skipping cycle")
        return

    conv_client = GHLClient(settings=settings, api_key_override=settings.ghl_conversations_api_key)
    contact_client = GHLClient(settings=settings)  # ghl_api_key — contacts scope

    conversations = _discover_conversations(conv_client)
    logger.info("staff_call_quality_scan: %d candidate conversation(s)", len(conversations))

    processed = skipped = 0

    for conv in conversations:
        if processed >= _PER_CYCLE_CAP:
            logger.info("staff_call_quality_scan: per-cycle cap reached, remainder deferred to next run")
            break

        conv_id = conv.get("id")
        contact_id = conv.get("contactId")
        phone = conv.get("phone")
        if not conv_id or not contact_id:
            continue

        try:
            messages = conv_client.get_conversation_messages(conv_id, limit=50)
            # Isolated with the fetch: an unexpected response shape (e.g. the
            # double-nested 'messages' quirk fixed 2026-08-27 — see spec/23)
            # must not crash the whole cycle over one conversation's worth of
            # per-item work — matches the module's "per-item failures are
            # isolated" contract.
            call_messages = [m for m in messages if isinstance(m, dict) and (m.get("messageType") or m.get("type")) == "TYPE_CALL"]
            new_messages = _filter_unprocessed(session, [m.get("id") for m in call_messages if m.get("id")])
        except Exception as exc:
            logger.warning("staff_call_quality_scan: could not process messages for conv=%s: %s", conv_id, exc)
            continue

        for message in call_messages:
            if message.get("id") not in new_messages:
                continue
            if processed >= _PER_CYCLE_CAP:
                break
            try:
                _process_call_message(
                    session, conv_client, contact_client, settings,
                    conv_id=conv_id, contact_id=contact_id, phone=phone, message=message,
                )
                session.commit()
                processed += 1
            except Exception as exc:
                logger.error(
                    "staff_call_quality_scan: failed to process message=%s conv=%s: %s",
                    message.get("id"), conv_id, exc, exc_info=True,
                )
                session.rollback()
                skipped += 1

    logger.info("staff_call_quality_scan: cycle done | processed=%d skipped=%d", processed, skipped)


def _discover_conversations(conv_client: Any) -> list[dict]:
    """
    Page forward through /conversations/search, ascending by last_message_date,
    starting from _LOOKBACK_HOURS ago, until a page comes back short (caught
    up to "now") or _MAX_DISCOVERY_PAGES is hit (safety cap on GHL read
    volume per cycle — not a limit on how many calls get *scored*, which
    _PER_CYCLE_CAP in _run_scan_cycle already bounds separately).

    No last_message_type filter — see module docstring and spec/23 for why
    (a call immediately followed by a rep's note must still surface).

    GHL's start_after_date is a pagination cursor ("the sort value of the
    last document"), not a "since this time" filter, and its default sort
    is descending by recency — so this explicitly requests ascending sort
    and treats the lookback timestamp as the *starting* cursor, paginating
    forward toward "now" rather than backward into history. Confirmed live
    2026-08-27 that the naive descending-default usage silently walks
    backward into a stale window instead.
    """
    lookback_start = datetime.now(tz=timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
    cursor: int | None = int(lookback_start.timestamp() * 1000)

    # GHL's start_after_date cursor is inclusive of an exact-match boundary
    # item (confirmed live: re-using the last item's date returns that same
    # item again as the next page's first result) — dedupe by conversation
    # id defensively rather than assuming exactly one boundary duplicate.
    conversations: list[dict] = []
    seen_ids: set[str] = set()
    for _ in range(_MAX_DISCOVERY_PAGES):
        page = conv_client.search_conversations(
            start_after_date=cursor,
            sort_by="last_message_date",
            sort="asc",
            limit=_DISCOVERY_PAGE_SIZE,
        )
        if not page:
            break
        for conv in page:
            conv_id = conv.get("id")
            if conv_id and conv_id in seen_ids:
                continue
            if conv_id:
                seen_ids.add(conv_id)
            conversations.append(conv)
        if len(page) < _DISCOVERY_PAGE_SIZE:
            break  # short page — caught up to "now", no more pages to fetch
        cursor = page[-1].get("lastMessageDate")
        if cursor is None:
            logger.warning(
                "staff_call_quality_scan: last page item missing lastMessageDate — "
                "cannot continue pagination, stopping discovery early"
            )
            break
    else:
        logger.warning(
            "staff_call_quality_scan: hit _MAX_DISCOVERY_PAGES=%d — more conversations "
            "may exist in the lookback window than this cycle covered; remainder picked "
            "up on a later cycle since the lookback window is rolling",
            _MAX_DISCOVERY_PAGES,
        )

    return conversations


def _filter_unprocessed(session: Session, message_ids: list[str]) -> set[str]:
    """Return the subset of message_ids not already in staff_call_quality."""
    from sqlalchemy import select

    from app.models.staff_call_quality import StaffCallQuality

    if not message_ids:
        return set()
    existing = set(session.scalars(
        select(StaffCallQuality.ghl_message_id).where(
            StaffCallQuality.ghl_message_id.in_(message_ids)
        )
    ).all())
    return set(message_ids) - existing


def _process_call_message(
    session: Session,
    conv_client: Any,
    contact_client: Any,
    settings: Any,
    *,
    conv_id: str,
    contact_id: str,
    phone: Optional[str],
    message: dict,
) -> None:
    from app.adapters.ghl import GHLError
    from app.adapters.openai_client import OpenAIClient
    from app.core.call_classification import (
        classify_from_known_signals,
        classify_from_transcript_ai,
        extract_classification_signals,
    )
    from app.core.call_quality_scoring import score_call
    from app.core.ghl_support_context import extract_support_context
    from app.models.staff_call_quality import StaffCallQuality

    message_id = message["id"]
    call_meta = (message.get("meta") or {}).get("call") or {}
    duration = call_meta.get("duration")
    status = call_meta.get("status")
    call_time = _parse_ghl_datetime(message.get("dateAdded"))
    call_connected = status == "completed" and bool(duration) and duration >= _MIN_CONNECTED_DURATION_SECONDS

    row = StaffCallQuality(
        id=str(uuid.uuid4()),
        ghl_message_id=message_id,
        ghl_conversation_id=conv_id,
        ghl_contact_id=contact_id,
        lead_phone=phone,  # from the conversation object; refined below if the contact fetch succeeds
        rep_user_id=message.get("userId"),
        call_time=call_time or datetime.now(tz=timezone.utc),
        duration_seconds=duration,
        direction=message.get("direction"),
        call_connected=call_connected,
    )

    if not call_connected:
        session.add(row)
        logger.info("staff_call_quality_scan: not connected (status=%s) | message=%s", status, message_id)
        return

    # ── Transcript: GHL native first, Whisper fallback ─────────────────────
    transcript_text = None
    transcript_source = None
    try:
        sentences = conv_client.get_message_transcription(message_id)
        if sentences:
            transcript_text = " ".join(s.get("transcript", "") for s in sentences if s.get("transcript"))
            transcript_source = "ghl_native"
    except GHLError as exc:
        logger.warning("staff_call_quality_scan: GHL transcription lookup failed for %s: %s", message_id, exc)

    if not transcript_text:
        try:
            audio_bytes = conv_client.get_message_recording(message_id)
            openai_client = OpenAIClient(settings=settings)
            transcript_text = openai_client.transcribe_audio(audio_bytes, filename=f"{message_id}.wav")
            transcript_source = "openai_whisper"
        except GHLError as exc:
            logger.warning("staff_call_quality_scan: no recording available for %s: %s", message_id, exc)
        except Exception as exc:
            logger.warning("staff_call_quality_scan: transcription failed for %s: %s", message_id, exc)

    row.transcript_text = transcript_text
    row.transcript_source = transcript_source

    if not transcript_text:
        row.flagged_reason = "Connected call but no transcript could be obtained (no recording, or transcription failed)"
        session.add(row)
        return

    # ── Classification ──────────────────────────────────────────────────────
    try:
        # GHL wraps this response ({"contact": {...}}) — every other caller
        # in this codebase unwraps immediately after fetching; this one
        # didn't, so extract_classification_signals/resolve_field_id/
        # extract_support_context (which all expect the unwrapped shape)
        # were silently reading tags/customFields as always-empty. Confirmed
        # live 2026-08-27: 0 of 136 classified rows used ghl_tags or any
        # other known-signal source — every one fell through to the AI
        # transcript fallback. See spec/23.
        contact = contact_client.get_contact(contact_id).get("contact", {})
    except Exception as exc:
        logger.warning("staff_call_quality_scan: could not fetch contact=%s: %s", contact_id, exc)
        contact = {}

    lead_first = contact.get("firstName") or ""
    lead_last = contact.get("lastName") or ""
    lead_name = f"{lead_first} {lead_last}".strip() or contact.get("contactName") or None
    if lead_name:
        row.lead_name = lead_name
    if contact.get("phone"):
        row.lead_phone = contact["phone"]

    signals = extract_classification_signals(contact)
    has_history = _has_enrolled_call_history(session, phone) if phone else False
    conversation_type, source = classify_from_known_signals(
        tags=signals["tags"],
        who_you_are_value=signals["who_you_are_value"],
        select_option_1_value=signals["select_option_1_value"],
        select_option_2_value=signals["select_option_2_value"],
        enrollment_date=signals["enrollment_date"],
        has_enrolled_call_history=has_history,
    )

    openai_client = OpenAIClient(settings=settings)
    if conversation_type == "unknown":
        conversation_type, source = classify_from_transcript_ai(transcript_text, openai_client)

    row.conversation_type = conversation_type
    row.conversation_type_source = source
    row.ghl_tags = signals["tags"]
    row.who_you_are_value = signals["who_you_are_value"]
    row.select_option_1_value = signals["select_option_1_value"]
    row.select_option_2_value = signals["select_option_2_value"]
    row.ghl_enrollment_date = signals["enrollment_date"]

    # ── Scoring ──────────────────────────────────────────────────────────────
    support_context = extract_support_context(contact) if conversation_type == "support" else None
    result = score_call(
        transcript=transcript_text,
        conversation_type=conversation_type,
        duration_seconds=duration,
        call_connected=call_connected,
        client=openai_client,
        support_context=support_context,
    )
    if result:
        row.quality_score = result.get("overall_score")
        row.rubric_json = result
        row.summary = result.get("summary")
        row.flagged_reason = result.get("flagged_reason")

    session.add(row)
    logger.info(
        "staff_call_quality_scan: processed | message=%s type=%s(%s) score=%s",
        message_id, conversation_type, source, row.quality_score,
    )


def _has_enrolled_call_history(session: Session, phone: str) -> bool:
    """Check Cora's own call_events for an 'enrolled' intent tied to this phone."""
    row = session.execute(text("""
        SELECT 1 FROM call_events
        WHERE detected_intent = 'enrolled'
          AND (
              contact_id = :phone
              OR contact_id IN (SELECT contact_id FROM lead_state WHERE normalized_phone = :phone)
          )
        LIMIT 1
    """), {"phone": phone}).fetchone()
    return row is not None


def _parse_ghl_datetime(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _reschedule(session: Session) -> None:
    from app.worker.scheduler import schedule_job

    run_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_SCHEDULE_INTERVAL_SECONDS)
    schedule_job(
        session=session,
        job_type="staff_call_quality_scan",
        entity_type="system",
        entity_id="staff_call_quality_scan",
        run_at=run_at,
        payload={"_scheduled_by": "self"},
    )
    logger.debug("staff_call_quality_scan: rescheduled in %ds", _SCHEDULE_INTERVAL_SECONDS)


def start_staff_call_quality_scanner() -> None:
    """Enqueue the first staff_call_quality_scan job. Call once from worker startup."""
    from app.db import get_sync_session
    from app.worker.scheduler import schedule_job

    with get_sync_session() as session:
        existing = session.execute(text("""
            SELECT id FROM scheduled_jobs
            WHERE job_type = 'staff_call_quality_scan' AND status = 'pending'
            LIMIT 1
        """)).fetchone()

        if existing:
            logger.info(
                "start_staff_call_quality_scanner: job already pending (id=%s) — skipping", existing[0],
            )
            return

        schedule_job(
            session=session,
            job_type="staff_call_quality_scan",
            entity_type="system",
            entity_id="staff_call_quality_scan",
            run_at=datetime.now(tz=timezone.utc),
            payload={"_scheduled_by": "startup"},
        )
        session.commit()
        logger.info("start_staff_call_quality_scanner: job enqueued")
