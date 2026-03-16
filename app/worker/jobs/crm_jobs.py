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
    Create a GHL follow-up task for a completed call.

    1. Claim the job
    2. Load CallEvent by call_event_id from payload
    3. Dedupe: skip if TaskEvent with status='created' already exists
    4. Call GHLClient.create_task() (shadow-gated)
    5. Record TaskEvent row
    6. Complete job
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

        try:
            logger.info(
                "create_crm_task | job_id=%s call_event_id=%s", job_id, call_event_id
            )

            if not call_event_id:
                raise ValueError(f"Missing call_event_id in job payload | job_id={job_id}")

            from sqlalchemy import select

            from app.models.call_event import CallEvent
            from app.models.task_event import TaskEvent

            call_event = session.get(CallEvent, call_event_id)
            if call_event is None:
                raise ValueError(f"CallEvent not found | call_event_id={call_event_id}")

            contact_id = call_event.contact_id or ""
            effective_call_id = call_id or call_event.call_id

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

            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
            result = ghl.create_task(
                contact_id=contact_id or "unknown",
                title=f"Completed call — {effective_call_id}",
                description=f"Automated follow-up for completed call {effective_call_id}",
            )

            provider_task_id = result.get("id") if not result.get("shadow") else None
            task_event = TaskEvent(
                id=str(uuid.uuid4()),
                call_event_id=call_event_id,
                provider_task_id=provider_task_id,
                status="created",
                created_at=datetime.now(tz=timezone.utc),
            )
            session.add(task_event)
            session.flush()

            logger.info(
                "create_crm_task: done | call_event_id=%s shadow=%s",
                call_event_id, result.get("shadow", False),
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
