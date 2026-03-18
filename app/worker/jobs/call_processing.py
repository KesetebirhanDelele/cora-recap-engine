"""
Call event processing job.

Orchestrates the main processing path for a received call webhook:
  1. Claim the scheduled job atomically
  2. Normalize the Synthflow payload (call_status → internal status)
  3. Create a CallEvent row (idempotent via dedupe_key)
  4. Log any executed_actions failures from the Synthflow payload
  5. Route: call-through path or voicemail path
  6. Enqueue downstream jobs with call_event_id propagated
  7. On failure: create exception record, mark job failed

This job runs on the `default` RQ queue.

Field mapping (Synthflow → internal):
  call_status  → CallEvent.status  (primary routing field)
  status       → fallback if call_status absent (backwards compat / tests)
  end_call_reason → CallEvent.end_call_reason
  transcript   → CallEvent.transcript
  recording_url → CallEvent.recording_url
  duration     → CallEvent.duration_seconds
  model_id     → CallEvent.model_id
  lead_name    → CallEvent.lead_name
  agent_phone_number → CallEvent.agent_phone_number
  timeline     → CallEvent.timeline
  telephony_duration → CallEvent.telephony_duration
  telephony_start → CallEvent.telephony_start
  telephony_end   → CallEvent.telephony_end
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)

# Call status values that route to the voicemail path.
# All Synthflow variants that indicate the call reached a machine/voicemail inbox.
VOICEMAIL_STATUSES = frozenset({
    "voicemail",
    "hangup_on_voicemail",
    "left_voicemail",
    "voicemail_detected",
    "machine_detected",
})
# Private alias for internal routing (module-level name kept for backwards compat)
_VOICEMAIL_STATUSES = VOICEMAIL_STATUSES

# Call status values that route to the call-through path
_COMPLETED_STATUSES = frozenset({"completed"})

# Statuses that are transient — may recover with a retry
_PENDING_STATUSES = frozenset({"queue", "in-progress"})


def normalize_synthflow_outcome(payload: dict[str, Any]) -> str:
    """
    Extract the canonical routing status from a Synthflow completed-call payload.

    Tries multiple field aliases in priority order — Synthflow payloads are not
    always consistent across workflow versions:
      call_status → Status → status → state → event

    Both the status field and end_call_reason are considered so that combinations
    like call_status=hangup_on_voicemail + end_call_reason=voicemail route correctly.

    If no status can be determined the call is defaulted to 'completed' with a
    warning so that the worker always writes a row rather than crashing.

    Returns one of: completed | voicemail | hangup_on_voicemail | left_voicemail |
                    voicemail_detected | machine_detected | failed | <raw>
    """
    _STATUS_ALIASES = ("call_status", "Status", "status", "state", "event")
    call_status = ""
    for alias in _STATUS_ALIASES:
        value = payload.get(alias)
        if value:
            call_status = str(value).strip()
            break

    end_call_reason = payload.get("end_call_reason", "")

    # Voicemail signal: either field indicates voicemail
    if call_status in _VOICEMAIL_STATUSES or end_call_reason in ("voicemail",):
        return call_status if call_status in _VOICEMAIL_STATUSES else "voicemail"

    if not call_status:
        logger.warning(
            "normalize_synthflow_outcome: no status field found in payload, "
            "defaulting to 'completed' | aliases_tried=%s",
            _STATUS_ALIASES,
        )
        return "completed"

    return call_status


def _parse_datetime(value: Any) -> datetime | None:
    """
    Parse an ISO string or epoch int/float into a UTC datetime, or return None.

    Synthflow timestamps are sometimes milliseconds since epoch (13-digit integers)
    rather than seconds (10-digit). Values > 1e11 are treated as milliseconds.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # Detect millisecond epoch: values larger than year ~5138 in seconds
        if value > 1e11:
            value = value / 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _log_executed_actions(call_id: str, payload: dict[str, Any]) -> None:
    """
    Inspect executed_actions from a completed-call payload and log any failures.

    Per spec 14: failed in-agent external actions must be logged and retained.
    They must never silently masquerade as successful enrichment.
    """
    actions = payload.get("executed_actions")
    if not actions:
        return
    for action in (actions if isinstance(actions, list) else [actions]):
        status_code = action.get("status_code") or action.get("statusCode")
        action_name = action.get("name") or action.get("action") or "unknown"
        if status_code and int(status_code) >= 400:
            logger.warning(
                "executed_action FAILED in Synthflow agent | call_id=%s action=%s status=%s",
                call_id, action_name, status_code,
            )
        else:
            logger.debug(
                "executed_action ok | call_id=%s action=%s status=%s",
                call_id, action_name, status_code,
            )


def _create_call_event(session, call_id: str, payload: dict[str, Any], status: str):
    """
    Persist a CallEvent row from a Synthflow completed-call payload.

    Uses dedupe_key = "{call_id}:process_call_event" so replays are safe.
    Returns the CallEvent (existing or newly created).
    """
    from sqlalchemy import select

    from app.models.call_event import CallEvent

    dedupe_key = f"{call_id}:process_call_event"

    existing = session.scalars(
        select(CallEvent).where(CallEvent.dedupe_key == dedupe_key)
    ).first()
    if existing:
        logger.info(
            "_create_call_event: dedupe hit, reusing existing | call_id=%s id=%s",
            call_id, existing.id,
        )
        return existing

    # Parse start_time — Synthflow uses 'start_time' (ISO or epoch)
    start_time_raw = payload.get("start_time") or payload.get("start_time_utc")
    start_time = _parse_datetime(start_time_raw)

    # duration — Synthflow uses 'duration' (seconds, may be float)
    duration_raw = payload.get("duration") or payload.get("duration_seconds")
    duration_seconds = int(duration_raw) if duration_raw is not None else None

    event = CallEvent(
        id=str(uuid.uuid4()),
        call_id=call_id,
        contact_id=payload.get("contact_id"),
        direction=payload.get("direction", "outbound"),
        status=status,
        end_call_reason=payload.get("end_call_reason"),
        transcript=payload.get("transcript"),
        duration_seconds=duration_seconds,
        recording_url=payload.get("recording_url"),
        start_time_utc=start_time,
        # Synthflow-specific fields
        model_id=payload.get("model_id"),
        lead_name=payload.get("lead_name"),
        agent_phone_number=payload.get("agent_phone_number"),
        timeline=payload.get("timeline"),
        telephony_duration=payload.get("telephony_duration"),
        telephony_start=_parse_datetime(payload.get("telephony_start")),
        telephony_end=_parse_datetime(payload.get("telephony_end")),
        dedupe_key=dedupe_key,
        raw_payload_json=payload,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(event)
    session.flush()
    logger.info(
        "_create_call_event: created | call_id=%s id=%s status=%s",
        call_id, event.id, status,
    )
    return event


def process_call_event(job_id: str) -> None:
    """
    Main call event processing job function.

    job_id: the ScheduledJob.id from the scheduled_jobs table.

    Flow:
      claim → normalize status → create CallEvent → log executed_actions
      → route → enqueue downstream (with call_event_id) → complete
      any unrecoverable failure → exception record + fail job
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        # 1. Claim the job atomically
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info(
                "process_call_event: job already claimed or not found | job_id=%s",
                job_id,
            )
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_id = payload.get("call_id")

        try:
            if not call_id:
                raise ValueError(f"Missing call_id in job payload | job_id={job_id}")

            # 2. Normalize routing status (handles call_status vs status field)
            call_status = normalize_synthflow_outcome(payload)
            contact_id = payload.get("contact_id")

            logger.info(
                "process_call_event | job_id=%s call_id=%s status=%s",
                job_id, call_id, call_status,
            )

            # 3. Persist CallEvent row (idempotent)
            call_event = _create_call_event(session, call_id, payload, call_status)

            # 4. Log any in-agent action failures
            _log_executed_actions(call_id, payload)

            # 5. Route by normalized status
            if call_status in _COMPLETED_STATUSES:
                _route_to_call_through(
                    session, job, call_id, contact_id, call_event.id, settings
                )
            elif call_status in _VOICEMAIL_STATUSES:
                _route_to_voicemail(
                    session, job, call_id, contact_id, call_event.id, settings,
                    campaign_name=payload.get("campaign_name"),
                    lead_name=payload.get("lead_name", "") or payload.get("name", ""),
                )
            elif call_status in _PENDING_STATUSES:
                logger.warning(
                    "process_call_event: call still in progress | call_id=%s status=%s",
                    call_id, call_status,
                )
                create_exception(
                    session,
                    type="call_pending",
                    severity="warning",
                    context={"call_id": call_id, "status": call_status, "job_id": job_id},
                    entity_type="call",
                    entity_id=call_id,
                )
            else:
                # Unknown status — log, write the call_event row (already done), then
                # route to call-through as the safest default so the call is not silently
                # dropped. An exception record is created for operator visibility.
                logger.warning(
                    "process_call_event: unrecognised status %r, defaulting to "
                    "call-through path | call_id=%s job_id=%s",
                    call_status, call_id, job_id,
                )
                create_exception(
                    session,
                    type="unknown_call_status",
                    severity="warning",
                    context={
                        "call_id": call_id,
                        "status": call_status,
                        "job_id": job_id,
                    },
                    entity_type="call",
                    entity_id=call_id,
                )
                _route_to_call_through(
                    session, job, call_id, contact_id, call_event.id, settings
                )

            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "process_call_event: unhandled error | job_id=%s call_id=%s: %s",
                job_id, call_id, exc,
            )
            create_exception(
                session,
                type="call_processing_failed",
                severity="critical",
                context={"call_id": call_id, "job_id": job_id, "error": str(exc)},
                entity_type="call",
                entity_id=call_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


def _make_default_queue(settings):
    """
    Build an RQ Queue for the `default` queue using settings.

    Used to immediately enqueue voicemail tier jobs so they are processed
    without waiting for the worker recovery loop.
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
        logger.warning(
            "_make_default_queue: Redis unavailable, job will stay pending | %s", exc
        )
        return None


def _make_ai_queue(settings):
    """
    Build an RQ Queue for the `ai` queue using settings.

    Called inside worker jobs where FastAPI's app.state is not available.
    Returns None if Redis is unreachable (job stays pending in Postgres for
    the recovery loop to pick up on next worker start).
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
        return Queue(settings.rq_ai_queue, connection=conn)
    except Exception as exc:
        logger.warning("_make_ai_queue: Redis unavailable, job will stay pending | %s", exc)
        return None


def _route_to_call_through(
    session, job, call_id: str, contact_id: str | None, call_event_id: str, settings
) -> None:
    """
    Schedule a classify_call_event job for a completed non-voicemail call.

    Enqueues to the `ai` RQ queue so the worker picks it up immediately.
    Falls back to Postgres-only (recovery loop) if Redis is unavailable.
    Propagates call_event_id so ai_jobs can load the transcript.
    """
    from app.worker.jobs.ai_jobs import classify_call_event
    from app.worker.scheduler import schedule_job

    ai_queue = _make_ai_queue(settings)

    logger.info("process_call_event: routing to call-through | call_id=%s", call_id)
    schedule_job(
        session=session,
        job_type="run_call_analysis",
        entity_type="call",
        entity_id=call_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id": call_id,
            "contact_id": contact_id,
            "call_event_id": call_event_id,
            "parent_job_id": job.id,
        },
        rq_queue=ai_queue,
        rq_job_func=classify_call_event if ai_queue is not None else None,
    )


def _route_to_voicemail(
    session, job, call_id: str, contact_id: str | None, call_event_id: str, settings,
    campaign_name: str | None = None,
    lead_name: str = "",
) -> None:
    """
    Schedule a voicemail tier advancement job.

    Enqueues to the `default` RQ queue so the worker picks it up immediately.
    Falls back to Postgres-only (recovery loop) if Redis is unavailable.
    Propagates call_event_id, campaign_name, and lead_name for downstream use.

    campaign_name is forwarded so that process_voicemail_tier can populate
    lead_state.campaign_name when auto-creating a row for a new contact.
    In production this is typically already set on the lead_state row from GHL.
    For testing, pass campaign_name in the webhook payload.

    lead_name is forwarded so that the eventual launch_outbound_call_job can
    pass the contact's name to the Synthflow launch webhook.
    """
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier
    from app.worker.scheduler import schedule_job

    default_queue = _make_default_queue(settings)

    logger.info("process_call_event: routing to voicemail | call_id=%s", call_id)
    schedule_job(
        session=session,
        job_type="process_voicemail_tier",
        entity_type="call",
        entity_id=call_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id": call_id,
            "contact_id": contact_id,
            "call_event_id": call_event_id,
            "parent_job_id": job.id,
            "campaign_name": campaign_name,
            "lead_name": lead_name,
        },
        rq_queue=default_queue,
        rq_job_func=process_voicemail_tier if default_queue is not None else None,
    )
