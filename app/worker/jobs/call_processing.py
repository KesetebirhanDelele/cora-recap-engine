"""
Call event processing job.

Orchestrates the main processing path for a received call webhook:
  1. Claim the scheduled job atomically
  2. Enrich the call event from GHL (get contact by phone/ID)
  3. Route: call-through path or voicemail path
  4. Enqueue downstream jobs (AI analysis or tier engine)
  5. On failure: create exception record, mark job failed

This job runs on the `default` RQ queue.

Phase 6: routing and enrichment logic wired. AI calls delegated to ai_jobs.
Phase 7: voicemail tier advancement with Synthflow scheduling added.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)

# Call status values that route to the voicemail path
_VOICEMAIL_STATUSES = frozenset({"voicemail", "hangup_on_voicemail"})

# Call status values that route to the call-through path
_COMPLETED_STATUSES = frozenset({"completed"})

# Statuses that are transient — may recover with a retry
_PENDING_STATUSES = frozenset({"queue", "in-progress"})


def process_call_event(job_id: str) -> None:
    """
    Main call event processing job function.

    job_id: the ScheduledJob.id from the scheduled_jobs table.

    Flow:
      claim → enrich → route → enqueue downstream → complete
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
        call_status = payload.get("status", "")
        contact_id = payload.get("contact_id")

        try:
            if not call_id:
                raise ValueError(f"Missing call_id in job payload | job_id={job_id}")

            logger.info(
                "process_call_event | job_id=%s call_id=%s status=%s",
                job_id, call_id, call_status,
            )

            # 2. Route by call status
            if call_status in _COMPLETED_STATUSES:
                _route_to_call_through(session, job, call_id, contact_id, settings)
            elif call_status in _VOICEMAIL_STATUSES:
                _route_to_voicemail(session, job, call_id, contact_id, settings)
            elif call_status in _PENDING_STATUSES:
                # Transient — will be retried by the retry job mechanism
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
                raise ValueError(
                    f"Unknown call status: {call_status!r} | call_id={call_id}"
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


def _route_to_call_through(session, job, call_id, contact_id, settings) -> None:
    """
    Schedule an AI analysis job for a completed non-voicemail call.
    Enqueues to the AI queue for analysis, summary, consent detection, and task creation.
    """
    from datetime import datetime, timezone

    from app.worker.scheduler import schedule_job

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
            "parent_job_id": job.id,
        },
    )


def _route_to_voicemail(session, job, call_id, contact_id, settings) -> None:
    """
    Schedule a voicemail tier advancement job.
    Phase 7 wires the full tier engine + Synthflow scheduling.
    """
    from datetime import datetime, timezone

    from app.worker.scheduler import schedule_job

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
            "parent_job_id": job.id,
        },
    )
