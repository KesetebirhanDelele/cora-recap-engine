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


def _resolve_lead_state(session, contact_id: str | None, call_event):
    """
    Return the LeadState for a contact, with a phone-number fallback.

    Priority:
      1. Direct contact_id match (standard outbound path).
      2. normalized_phone match using phone fields from call_event.raw_payload_json
         (inbound path — contact_id is a phone string, real row uses a GHL ID).

    Returns None only when no row can be found via either lookup.
    """
    from sqlalchemy import select
    from app.models.lead_state import LeadState

    if not contact_id:
        return None

    lead = session.scalars(
        select(LeadState).where(LeadState.contact_id == contact_id)
    ).first()
    if lead is not None:
        return lead

    # Fallback: look up by normalised phone extracted from the call payload.
    raw = (call_event.raw_payload_json or {}) if call_event else {}
    phone = (
        raw.get("phone_number_from")
        or raw.get("phone_number_to")
        or raw.get("phone_number")
        or raw.get("phone")
        or (contact_id if contact_id.startswith("+") else None)
    )
    if not phone:
        return None

    return session.scalars(
        select(LeadState).where(LeadState.normalized_phone == phone)
    ).first()


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
        payload_campaign_name = payload.get("campaign_name") or ""

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

            # ── Live-call intent routing ───────────────────────────────────────
            # Only run for completed (answered) calls. Voicemail-status calls
            # are routed to process_voicemail_tier and never reach this job.
            # The explicit status check is a safety guard for replays or edge cases.
            if transcript and call_event and call_event.status == "completed":
                from app.core.intent_detection import detect_intent
                from app.core.intent_actions import handle_intent
                from app.core.campaigns import (
                    apply_campaign_switch,
                    evaluate_campaign_switch,
                )
                from app.models.lead_state import LeadState
                from sqlalchemy import select

                ea = (call_event.raw_payload_json or {}).get("executed_actions") if call_event else None
                dur = call_event.duration_seconds if call_event else None

                intent_result = detect_intent(
                    transcript,
                    executed_actions=ea,
                    duration_seconds=dur,
                )

                if intent_result is not None:
                    live_lead = _resolve_lead_state(session, contact_id, call_event)
                    # If phone-fallback matched a different row, use its contact_id
                    # for all downstream operations so intent actions hit the right row.
                    if live_lead is not None and live_lead.contact_id != contact_id:
                        contact_id = live_lead.contact_id

                    # ── Stub creation for brand-new inbound callers ────────────
                    # If no LeadState exists (not found by ID or phone), create a
                    # minimal row now so that:
                    #   - handle_intent's _update_lead_state finds a row to update
                    #   - update_lead_state job hits the upsert branch, not create
                    #   - campaign_name is stamped correctly from day one
                    if live_lead is None and contact_id:
                        raw_payload = (call_event.raw_payload_json or {}) if call_event else {}
                        derived_phone = (
                            raw_payload.get("phone_number_from")
                            or raw_payload.get("phone_number_to")
                            or raw_payload.get("phone_number")
                            or raw_payload.get("phone")
                            or (contact_id if contact_id.startswith("+") else None)
                        )
                        now_ts = datetime.now(tz=timezone.utc)
                        live_lead = LeadState(
                            id=str(uuid.uuid4()),
                            contact_id=contact_id,
                            normalized_phone=derived_phone,
                            campaign_name=payload_campaign_name or None,
                            status="active",
                            version=0,
                            created_at=now_ts,
                            updated_at=now_ts,
                        )
                        session.add(live_lead)
                        session.flush()
                        logger.info(
                            "run_call_analysis: created LeadState stub for new inbound caller "
                            "| contact_id=%s phone=%r campaign=%r",
                            contact_id, derived_phone, payload_campaign_name,
                        )

                    live_phone = live_lead.normalized_phone if live_lead else ""
                    campaign_name = (
                        (live_lead.campaign_name if live_lead else None)
                        or payload_campaign_name
                    )

                    logger.info(
                        "live_call_detected | contact_id=%s intent=%s",
                        contact_id, intent_result["intent"],
                    )

                    # Persist detected intent on the call_event row so the
                    # dashboard can display it without relying on scheduled_jobs.
                    if call_event is not None:
                        call_event.detected_intent = intent_result["intent"]
                        session.flush()

                    handle_intent(
                        session=session,
                        intent_result=intent_result,
                        contact_id=contact_id,
                        phone=live_phone or "",
                        current_job_id=job.id,
                        settings=settings,
                    )

                    new_campaign = evaluate_campaign_switch(
                        campaign_name or "", intent_result["intent"]
                    )
                    if new_campaign and live_lead is not None:
                        session.refresh(live_lead)
                        # Guard: do not switch campaign while a lead is mid-voicemail-
                        # sequence.  A New Lead that hasn't finished their voicemail
                        # tier progression (ai_campaign_value is set but not terminal
                        # "3") should maintain their campaign label until the sequence
                        # completes.  Cold Lead → New Lead re-engagement upgrades are
                        # exempt because they are always desirable regardless of tier.
                        tier = live_lead.ai_campaign_value
                        in_voicemail_sequence = tier is not None and tier != "3"
                        is_upgrade = new_campaign == "New Lead"
                        if not in_voicemail_sequence or is_upgrade:
                            apply_campaign_switch(
                                session, live_lead, new_campaign,
                                reason=intent_result["intent"],
                            )
                        else:
                            logger.info(
                                "campaign_switch_deferred: lead mid-voicemail-sequence "
                                "(tier=%r), switch %r→%r deferred until sequence ends | "
                                "contact_id=%s intent=%s",
                                tier, campaign_name, new_campaign,
                                contact_id, intent_result["intent"],
                            )

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
