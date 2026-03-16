"""
AI analysis job — runs on the `ai` RQ queue.

Executes call analysis, student summary generation, and consent detection
for a completed non-voicemail call. Writes outputs to classification_results
and summary_results tables.

After AI results are persisted, three downstream jobs are scheduled:
  - create_crm_task    → callbacks queue (Feature 2)
  - send_student_summary → callbacks queue (Feature 3, consent-gated at job level)
  - update_lead_state  → default queue   (Feature 4)

Consent gate: summary writeback to GHL occurs ONLY when consent == 'YES'.
              Enforced in send_student_summary (crm_jobs.py).

Phase 6: full AI orchestration wired. GHL task creation shadow-gated.
Phase 4 GHL client is used for task creation.
Phase 5 AI service is used for analysis, summary, consent.
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


def run_call_analysis(job_id: str) -> None:
    """
    AI analysis job for a completed call.

    1. Claim the job
    2. Load transcript from call_events
    3. generate_call_analysis → ClassificationResult row
    4. generate_student_summary → SummaryResult row
    5. detect_consent → update SummaryResult.summary_consent
    6. Schedule create_crm_task (callbacks queue, shadow-gated)
    7. Schedule send_student_summary (callbacks queue, consent-gated inside job)
    8. Schedule update_lead_state (default queue)
    9. Complete job
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("run_call_analysis: job already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_id = payload.get("call_id", "")
        call_event_id = payload.get("call_event_id")
        contact_id = payload.get("contact_id")

        try:
            logger.info(
                "run_call_analysis | job_id=%s call_id=%s", job_id, call_id
            )

            # Load call event for transcript
            from app.models.call_event import CallEvent

            call_event = None
            if call_event_id:
                call_event = session.get(CallEvent, call_event_id)

            transcript = call_event.transcript if call_event else ""

            # AI analysis
            from app.adapters.openai_client import OpenAIClient
            from app.services.ai import (
                detect_consent,
                generate_call_analysis,
                generate_student_summary,
            )

            ai_client = OpenAIClient(settings=settings)

            analysis = generate_call_analysis(
                transcript=transcript or "",
                settings=settings,
                client=ai_client,
            )

            summary = generate_student_summary(
                transcript=transcript,
                settings=settings,
                client=ai_client,
            )

            consent = detect_consent(
                transcript=transcript,
                settings=settings,
                client=ai_client,
            )

            # Persist results
            _persist_classification(session, call_event_id, analysis)
            _persist_summary(session, call_event_id, summary, consent)

            # Schedule downstream jobs (all run after this job completes)
            callbacks_queue = _make_callbacks_queue(settings)
            default_queue = _make_default_queue(settings)

            # Feature 2: CRM task creation
            if settings.task_create_on_completed_call:
                _schedule_crm_task(
                    session, call_id, call_event_id, contact_id,
                    callbacks_queue, settings,
                )

            # Feature 3: Student summary delivery (consent gate inside the job)
            if settings.enable_student_summary_writeback:
                _schedule_send_summary(
                    session, call_id, call_event_id, contact_id,
                    callbacks_queue, settings,
                )

            # Feature 4: Lead lifecycle state update
            _schedule_update_lead_state(
                session, call_id, call_event_id, contact_id,
                default_queue, settings,
            )

            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "run_call_analysis: error | job_id=%s call_id=%s: %s",
                job_id, call_id, exc,
            )
            create_exception(
                session,
                type="call_analysis_failed",
                severity="critical",
                context={"call_id": call_id, "job_id": job_id, "error": str(exc)},
                entity_type="call",
                entity_id=call_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


def _persist_classification(session, call_event_id, analysis) -> None:
    """Store classification output to classification_results."""
    if not call_event_id:
        return
    from app.models.classification import ClassificationResult

    record = ClassificationResult(
        id=str(uuid.uuid4()),
        call_event_id=call_event_id,
        model_used=analysis.model_used,
        prompt_family=analysis.prompt_family,
        prompt_version=analysis.prompt_version,
        output_json=analysis.raw,
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(record)
    session.flush()


def _persist_summary(session, call_event_id, summary, consent) -> None:
    """Store summary + consent to summary_results (upsert by call_event_id)."""
    if not call_event_id:
        return
    from sqlalchemy import select

    from app.models.summary import SummaryResult

    existing = session.scalars(
        select(SummaryResult).where(SummaryResult.call_event_id == call_event_id)
    ).first()

    if existing:
        existing.student_summary = summary.student_summary
        existing.summary_offered = summary.summary_offered
        existing.summary_consent = consent.consent
        existing.model_used = summary.model_used
        existing.prompt_family = summary.prompt_family
        existing.prompt_version = summary.prompt_version
    else:
        record = SummaryResult(
            id=str(uuid.uuid4()),
            call_event_id=call_event_id,
            student_summary=summary.student_summary,
            summary_offered=summary.summary_offered,
            summary_consent=consent.consent,
            model_used=summary.model_used,
            prompt_family=summary.prompt_family,
            prompt_version=summary.prompt_version,
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(record)
    session.flush()


# ── Queue helpers ─────────────────────────────────────────────────────────────

def _make_callbacks_queue(settings):
    """
    Build an RQ Queue for the `callbacks` queue.

    Returns None if Redis is unreachable.
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
        return Queue(settings.rq_callback_queue, connection=conn)
    except Exception as exc:
        logger.warning("_make_callbacks_queue: Redis unavailable | %s", exc)
        return None


def _make_default_queue(settings):
    """
    Build an RQ Queue for the `default` queue.

    Returns None if Redis is unreachable.
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
        logger.warning("_make_default_queue: Redis unavailable | %s", exc)
        return None


# ── Downstream job schedulers ──────────────────────────────────────────────────

def _schedule_crm_task(
    session, call_id, call_event_id, contact_id, callbacks_queue, settings
) -> None:
    """Schedule create_crm_task on the callbacks queue."""
    from app.worker.jobs.crm_jobs import create_crm_task
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type="create_crm_task",
        entity_type="call",
        entity_id=call_id or call_event_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id": call_id,
            "call_event_id": call_event_id,
            "contact_id": contact_id,
            "parent_job_id": None,
        },
        rq_queue=callbacks_queue,
        rq_job_func=create_crm_task if callbacks_queue is not None else None,
    )


def _schedule_send_summary(
    session, call_id, call_event_id, contact_id, callbacks_queue, settings
) -> None:
    """Schedule send_student_summary on the callbacks queue."""
    from app.worker.jobs.crm_jobs import send_student_summary
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type="send_student_summary",
        entity_type="call",
        entity_id=call_id or call_event_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id": call_id,
            "call_event_id": call_event_id,
            "contact_id": contact_id,
            "parent_job_id": None,
        },
        rq_queue=callbacks_queue,
        rq_job_func=send_student_summary if callbacks_queue is not None else None,
    )


def _schedule_update_lead_state(
    session, call_id, call_event_id, contact_id, default_queue, settings
) -> None:
    """Schedule update_lead_state on the default queue."""
    from app.worker.jobs.lifecycle_jobs import update_lead_state
    from app.worker.scheduler import schedule_job

    schedule_job(
        session=session,
        job_type="update_lead_state",
        entity_type="call",
        entity_id=call_id or call_event_id,
        run_at=datetime.now(tz=timezone.utc),
        payload={
            "call_id": call_id,
            "call_event_id": call_event_id,
            "contact_id": contact_id,
            "parent_job_id": None,
        },
        rq_queue=default_queue,
        rq_job_func=update_lead_state if default_queue is not None else None,
    )


# ── Public alias ──────────────────────────────────────────────────────────────
# classify_call_event is the canonical name exposed to the rest of the system.
# run_call_analysis is kept for backwards compatibility with existing tests.
classify_call_event = run_call_analysis
