"""
CRM worker jobs — runs on the `callbacks` RQ queue.

create_crm_task      — Creates a GHL follow-up task for a completed call.
send_student_summary — Delivers student recap to GHL when consent=YES.

Both jobs are:
  - Shadow-gated: no live GHL writes unless GHL_WRITE_MODE=live
  - Idempotent: task_events dedupe for create_crm_task; audit check for summary
  - Retry-safe: full claim/fail/exception lifecycle
  - Runs on the `callbacks` queue to isolate GHL write traffic
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


# ── Feature 2: CRM Task Creation ─────────────────────────────────────────────

def create_crm_task(job_id: str) -> None:
    """
    Analyse a completed call with AI and write all results to GHL.

    Path 1 — Voice call completed:
      1. Claim the job
      2. Load CallEvent; resolve phone from lead_state or raw_payload_json
      3. Dedupe: skip if TaskEvent with status='created' already exists
      4. Run generate_ghl_call_analysis() → rich structured output
      5. Update GHL contact fields (shadow-gated):
           Mark as Lead, AI Lead Assign To, Support Issue Ticket #3,
           AI Lead Classification, AI Campaign
      6. If create_task=yes → create GHL task with AI title/description/
           assignedTo/dueDate (shadow-gated)
      7. Record TaskEvent row
      8. Complete job

    ⚠️ Conflict note: to avoid duplicate tasks, set
       TASK_CREATE_ON_COMPLETED_CALL=true (controlled by settings.task_create_on_completed_call).
       The old generic task creation is replaced by this AI-driven version.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("create_crm_task: job already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_event_id = payload.get("call_event_id", "")
        call_id = payload.get("call_id", "")
        contact_id = payload.get("contact_id", "")

        try:
            logger.info(
                "create_crm_task | job_id=%s call_event_id=%s", job_id, call_event_id
            )

            if not call_event_id:
                raise ValueError(f"Missing call_event_id in job payload | job_id={job_id}")

            from sqlalchemy import select

            from app.models.call_event import CallEvent
            from app.models.lead_state import LeadState
            from app.models.task_event import TaskEvent

            call_event = session.get(CallEvent, call_event_id)
            if call_event is None:
                raise ValueError(f"CallEvent not found | call_event_id={call_event_id}")

            effective_contact_id = contact_id or call_event.contact_id or ""

            # Resolve phone: lead_state → raw_payload_json → empty
            lead = session.scalars(
                select(LeadState).where(LeadState.contact_id == effective_contact_id)
            ).first() if effective_contact_id else None

            contact_phone = (
                (lead.normalized_phone if lead else None)
                or (call_event.raw_payload_json or {}).get("phone_number_to")
                or (call_event.raw_payload_json or {}).get("phone_number")
                or (call_event.raw_payload_json or {}).get("phone")
                or ""
            )

            # call_start_time: Synthflow sends Unix ms in raw_payload_json
            raw = call_event.raw_payload_json or {}
            call_start_time_ms = (
                raw.get("call_start_time")
                or raw.get("startTime")
                or raw.get("start_time")
            )
            try:
                call_start_time_ms = int(call_start_time_ms) if call_start_time_ms else None
            except (TypeError, ValueError):
                call_start_time_ms = None

            # Dedupe: only one successful task per call event
            existing = session.scalars(
                select(TaskEvent).where(
                    TaskEvent.call_event_id == call_event_id,
                    TaskEvent.status == "created",
                )
            ).first()
            if existing:
                logger.info(
                    "create_crm_task: task already exists, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            # ── AI analysis ──────────────────────────────────────────────────
            from app.core.ai_message_generator import generate_ghl_call_analysis

            analysis = generate_ghl_call_analysis(
                transcript=call_event.transcript or "",
                call_start_time_ms=call_start_time_ms,
                duration_seconds=call_event.duration_seconds,
                contact_phone=contact_phone,
                settings=settings,
            )

            logger.info(
                "create_crm_task: analysis done | contact_id=%s classification=%s ai_campaign=%s",
                effective_contact_id, analysis.lead_classification, analysis.ai_campaign,
            )

            # ── Fetch GHL contact (read — always live, used for field ID resolution) ──
            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
            ghl_contact: dict = {}
            if effective_contact_id:
                try:
                    ghl_contact = ghl.get_contact(effective_contact_id)
                    logger.info(
                        "create_crm_task: GHL contact fetched | contact_id=%s name=%r tags=%s",
                        effective_contact_id,
                        ghl_contact.get("contact", {}).get("name", ""),
                        ghl_contact.get("contact", {}).get("tags", []),
                    )
                except Exception as _read_exc:
                    logger.warning(
                        "create_crm_task: GHL contact fetch failed (non-fatal) | "
                        "contact_id=%s: %s",
                        effective_contact_id, _read_exc,
                    )
            elif contact_phone:
                try:
                    found = ghl.search_contact_by_phone(contact_phone)
                    if found:
                        ghl_contact = found
                        # Back-fill contact_id if we only had a phone
                        if not effective_contact_id:
                            effective_contact_id = found.get("id", effective_contact_id)
                        logger.info(
                            "create_crm_task: GHL contact resolved by phone | contact_id=%s",
                            effective_contact_id,
                        )
                except Exception as _read_exc:
                    logger.warning(
                        "create_crm_task: GHL phone search failed (non-fatal) | %s",
                        _read_exc,
                    )

            # ── GHL contact field updates ────────────────────────────────────
            field_updates: dict[str, str] = {}
            if settings.ghl_field_mark_as_lead:
                field_updates[settings.ghl_field_mark_as_lead] = (
                    "Yes" if analysis.is_lead_classification else "No"
                )
            if settings.ghl_field_ai_lead_assign_to and analysis.assign_to:
                field_updates[settings.ghl_field_ai_lead_assign_to] = analysis.assign_to
            if settings.ghl_field_support_ticket_3 and analysis.task_description:
                field_updates[settings.ghl_field_support_ticket_3] = analysis.task_description
            if settings.ghl_field_ai_lead_classification and analysis.lead_classification:
                field_updates[settings.ghl_field_ai_lead_classification] = analysis.lead_classification
            if settings.ghl_field_ai_campaign:
                field_updates[settings.ghl_field_ai_campaign] = analysis.ai_campaign

            if field_updates:
                ghl.update_contact_fields(
                    contact_id=effective_contact_id or "unknown",
                    field_updates=field_updates,
                )

            # ── GHL task creation ────────────────────────────────────────────
            task_result: dict = {"shadow": True}
            if analysis.create_task:
                task_result = ghl.create_task(
                    contact_id=effective_contact_id or "unknown",
                    title=analysis.task_title,
                    description=analysis.task_description,
                    assigned_to=analysis.assign_to,
                    due_date=analysis.task_due_date,
                )

            provider_task_id = task_result.get("id") if not task_result.get("shadow") else None
            task_event = TaskEvent(
                id=str(uuid.uuid4()),
                call_event_id=call_event_id,
                provider_task_id=provider_task_id,
                status="created",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(task_event)
            session.flush()

            # ── Persist GHL analysis to classification_results for timeline ──
            _persist_ghl_analysis(session, call_event_id, analysis)

            logger.info(
                "create_crm_task: done | call_event_id=%s shadow=%s create_task=%s",
                call_event_id, task_result.get("shadow", False), analysis.create_task,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "create_crm_task: error | job_id=%s call_event_id=%s: %s",
                job_id, call_event_id, exc,
            )
            create_exception(
                session,
                type="crm_task_failed",
                severity="warning",
                context={
                    "call_id": call_id,
                    "call_event_id": call_event_id,
                    "job_id": job_id,
                    "error": str(exc),
                },
                entity_type="call",
                entity_id=call_id or call_event_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


# ── Path 2: GHL update after VM-tier SMS/Email generation ────────────────────

def update_ghl_after_vm_message(job_id: str) -> None:
    """
    Write GHL contact fields after a VM-tier SMS or Email is generated.

    Path 2 — After VM-tier message generated:
      Fields written (shadow-gated):
        Mark as Lead    → "Yes"
        Support Ticket #2 → generated message body (SMS text or email subject)
        Message         → full generated message body
        Support Ticket #4 → lead classification tag from last analysis
        AI Campaign     → "Yes"

    Payload keys:
      contact_id, channel ('sms'|'email'), message_body, message_subject
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("update_ghl_after_vm_message: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        contact_id = payload.get("contact_id", "")
        channel = payload.get("channel", "sms")
        message_body = payload.get("message_body", "")
        message_subject = payload.get("message_subject", "")

        try:
            logger.info(
                "update_ghl_after_vm_message | job_id=%s contact_id=%s channel=%s",
                job_id, contact_id, channel,
            )

            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)

            # Fetch GHL contact (read — always live) for field ID resolution
            ghl_contact: dict = {}
            if contact_id:
                try:
                    ghl_contact = ghl.get_contact(contact_id)
                    logger.info(
                        "update_ghl_after_vm_message: GHL contact fetched | contact_id=%s",
                        contact_id,
                    )
                except Exception as _read_exc:
                    logger.warning(
                        "update_ghl_after_vm_message: GHL contact fetch failed (non-fatal) | "
                        "contact_id=%s: %s",
                        contact_id, _read_exc,
                    )

            # Ticket #2 carries a brief identifier; Message carries the full body
            ticket_2_value = message_subject if channel == "email" else message_body[:200]

            field_updates: dict[str, str] = {}
            if settings.ghl_field_mark_as_lead:
                field_updates[settings.ghl_field_mark_as_lead] = "Yes"
            if settings.ghl_field_support_ticket_2 and ticket_2_value:
                field_updates[settings.ghl_field_support_ticket_2] = ticket_2_value
            if settings.ghl_field_message and message_body:
                field_updates[settings.ghl_field_message] = message_body
            if settings.ghl_field_ai_campaign:
                field_updates[settings.ghl_field_ai_campaign] = "Yes"

            # Support Ticket #4: most recent lead classification from classification_results
            if settings.ghl_field_support_ticket_4:
                classification = _get_latest_classification(session, contact_id)
                if classification:
                    field_updates[settings.ghl_field_support_ticket_4] = classification

            if field_updates:
                ghl.update_contact_fields(
                    contact_id=contact_id or "unknown",
                    field_updates=field_updates,
                )

            logger.info(
                "update_ghl_after_vm_message: done | contact_id=%s fields=%s",
                contact_id, list(field_updates.keys()),
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "update_ghl_after_vm_message: error | job_id=%s contact_id=%s: %s",
                job_id, contact_id, exc,
            )
            create_exception(
                session,
                type="ghl_vm_message_update_failed",
                severity="warning",
                context={"contact_id": contact_id, "job_id": job_id, "error": str(exc)},
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


def _persist_ghl_analysis(session, call_event_id: str, analysis) -> None:
    """
    Store the GHL call analysis result into classification_results so that
    lead_classification and call_detailed_summary appear in the timeline.

    Uses prompt_family='ghl_call_analysis' to distinguish from the old
    lead_stage_classifier row that run_call_analysis inserts.
    No unique constraint on call_event_id — both rows coexist safely.
    """
    if not call_event_id:
        return
    try:
        from app.models.classification import ClassificationResult

        record = ClassificationResult(
            id=str(uuid.uuid4()),
            call_event_id=call_event_id,
            model_used="gpt-4o-mini",
            prompt_family="ghl_call_analysis",
            prompt_version="v1",
            output_json={
                "lead_classification":   analysis.lead_classification,
                "is_lead":               analysis.is_lead_classification,
                "ai_campaign":           analysis.ai_campaign,
                "call_detailed_summary": analysis.call_detailed_summary,
                "task_title":            analysis.task_title,
                "assign_to":             analysis.assign_to,
                "call_start_time":       analysis.call_start_time_formatted,
                "task_due_date":         analysis.task_due_date,
            },
            created_at=datetime.now(tz=timezone.utc),
        )
        session.add(record)
        session.flush()
    except Exception as exc:
        logger.error(
            "_persist_ghl_analysis: failed (non-fatal) | call_event_id=%s: %s",
            call_event_id, exc,
        )


def _get_latest_classification(session, contact_id: str) -> str | None:
    """
    Return the most recent lead_classification tag from classification_results
    for this contact, or None if not found.
    """
    try:
        from sqlalchemy import select

        from app.models.call_event import CallEvent
        from app.models.classification import ClassificationResult

        # Find the most recent call_event for this contact
        call_event = session.scalars(
            select(CallEvent)
            .where(CallEvent.contact_id == contact_id)
            .order_by(CallEvent.created_at.desc())
            .limit(1)
        ).first()
        if not call_event:
            return None

        cr = session.scalars(
            select(ClassificationResult)
            .where(ClassificationResult.call_event_id == call_event.id)
            .order_by(ClassificationResult.created_at.desc())
            .limit(1)
        ).first()
        if not cr or not cr.output_json:
            return None

        return (cr.output_json or {}).get("lead_classification") or None

    except Exception:
        return None


# ── Feature 3: Student Recap Delivery ────────────────────────────────────────

def send_student_summary(job_id: str) -> None:
    """
    Deliver the student recap to GHL when consent is YES.

    1. Claim the job
    2. Load SummaryResult by call_event_id
    3. Consent gate: summary_consent must equal 'YES' — any other value exits cleanly
    4. Write summary to GHL contact field (shadow-gated)
    5. Record audit log entry
    6. Complete job
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_student_summary: job already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_event_id = payload.get("call_event_id", "")
        call_id = payload.get("call_id", "")

        try:
            logger.info(
                "send_student_summary | job_id=%s call_event_id=%s", job_id, call_event_id
            )

            if not call_event_id:
                raise ValueError(f"Missing call_event_id in job payload | job_id={job_id}")

            from sqlalchemy import select

            from app.models.call_event import CallEvent
            from app.models.summary import SummaryResult

            summary = session.scalars(
                select(SummaryResult).where(
                    SummaryResult.call_event_id == call_event_id
                )
            ).first()

            if summary is None:
                logger.info(
                    "send_student_summary: no summary found, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            if summary.summary_consent != "YES":
                logger.info(
                    "send_student_summary: consent=%r (not YES), skipping | call_event_id=%s",
                    summary.summary_consent, call_event_id,
                )
                complete_job(session, job)
                return

            if not summary.student_summary:
                logger.info(
                    "send_student_summary: empty summary text, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            call_event = session.get(CallEvent, call_event_id)
            contact_id = call_event.contact_id if call_event else None

            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
            field_label = settings.ghl_field_student_summary or "Student Summary"
            result = ghl.update_contact_fields(
                contact_id=contact_id or "unknown",
                field_updates={field_label: summary.student_summary},
            )

            logger.info(
                "send_student_summary: delivered | call_event_id=%s contact_id=%s shadow=%s",
                call_event_id, contact_id, result.get("shadow", False),
            )

            _record_summary_audit(session, call_event_id, call_id, contact_id)
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "send_student_summary: error | job_id=%s call_event_id=%s: %s",
                job_id, call_event_id, exc,
            )
            create_exception(
                session,
                type="student_summary_delivery_failed",
                severity="warning",
                context={
                    "call_id": call_id,
                    "call_event_id": call_event_id,
                    "job_id": job_id,
                    "error": str(exc),
                },
                entity_type="call",
                entity_id=call_id or call_event_id,
            )
            fail_job(session, job, reason=str(exc))
            raise


def _record_summary_audit(
    session, call_event_id: str, call_id: str, contact_id: str | None
) -> None:
    """Append an audit log entry for the student summary delivery."""
    from app.models.audit import AuditLog

    record = AuditLog(
        id=str(uuid.uuid4()),
        entity_type="call",
        entity_id=call_id or call_event_id,
        action="student_summary_delivered",
        operator_id="system",
        context_json={
            "call_event_id": call_event_id,
            "contact_id": contact_id or "",
        },
        created_at=datetime.now(tz=timezone.utc),
    )
    session.add(record)
    session.flush()
