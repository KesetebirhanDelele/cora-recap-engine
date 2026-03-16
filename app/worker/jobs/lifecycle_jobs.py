"""
Lead lifecycle state machine job — runs on the `default` RQ queue.

update_lead_state: Updates the LeadState row after AI classification completes.

Reads the classification output_json to determine the new lead_stage and
applies it to the authoritative LeadState row using optimistic concurrency.
If no LeadState row exists for the contact yet, one is created (upsert).

This job must be scheduled after classify_call_event completes and only
when a contact_id is available for the call event.
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


def update_lead_state(job_id: str) -> None:
    """
    Update LeadState from the latest AI classification result.

    1. Claim the job
    2. Load ClassificationResult by call_event_id (latest by created_at)
    3. Extract lead_stage from output_json
    4. Load or create LeadState by contact_id
    5. Apply optimistic concurrency update (version check)
    6. Complete job
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("update_lead_state: job already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        call_event_id = payload.get("call_event_id", "")
        call_id = payload.get("call_id", "")
        contact_id = payload.get("contact_id", "")

        try:
            logger.info(
                "update_lead_state | job_id=%s call_event_id=%s contact_id=%s",
                job_id, call_event_id, contact_id,
            )

            if not call_event_id:
                raise ValueError(f"Missing call_event_id | job_id={job_id}")

            from sqlalchemy import select, update

            from app.models.call_event import CallEvent
            from app.models.classification import ClassificationResult
            from app.models.lead_state import LeadState

            # Load latest classification for this call event
            classification = session.scalars(
                select(ClassificationResult)
                .where(ClassificationResult.call_event_id == call_event_id)
                .order_by(ClassificationResult.created_at.desc())
            ).first()

            if classification is None:
                logger.info(
                    "update_lead_state: no classification found, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            # Extract lead_stage — supports both 'lead_stage' and 'stage' keys
            output = classification.output_json or {}
            new_lead_stage = output.get("lead_stage") or output.get("stage") or ""

            if not new_lead_stage:
                logger.info(
                    "update_lead_state: no lead_stage in output, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            # Resolve contact_id from payload or call_event
            call_event = session.get(CallEvent, call_event_id)
            resolved_contact_id = (
                contact_id
                or (call_event.contact_id if call_event else "")
            )
            last_call_status = call_event.status if call_event else ""

            if not resolved_contact_id:
                logger.info(
                    "update_lead_state: no contact_id available, skipping | call_event_id=%s",
                    call_event_id,
                )
                complete_job(session, job)
                return

            now = datetime.now(tz=timezone.utc)

            lead = session.scalars(
                select(LeadState).where(LeadState.contact_id == resolved_contact_id)
            ).first()

            if lead is not None:
                result = session.execute(
                    update(LeadState)
                    .where(
                        LeadState.id == lead.id,
                        LeadState.version == lead.version,
                    )
                    .values(
                        lead_stage=new_lead_stage,
                        last_call_status=last_call_status,
                        version=lead.version + 1,
                        updated_at=now,
                    )
                )
                session.flush()
                if result.rowcount == 0:
                    raise RuntimeError(
                        f"update_lead_state version conflict | lead_id={lead.id} "
                        f"expected_version={lead.version}"
                    )
                logger.info(
                    "update_lead_state: updated | contact_id=%s lead_stage=%r "
                    "last_call_status=%r",
                    resolved_contact_id, new_lead_stage, last_call_status,
                )
            else:
                # No existing row — create one
                lead = LeadState(
                    id=str(uuid.uuid4()),
                    contact_id=resolved_contact_id,
                    lead_stage=new_lead_stage,
                    last_call_status=last_call_status,
                    version=0,
                    created_at=now,
                    updated_at=now,
                )
                session.add(lead)
                session.flush()
                logger.info(
                    "update_lead_state: created | contact_id=%s lead_stage=%r",
                    resolved_contact_id, new_lead_stage,
                )

            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "update_lead_state: error | job_id=%s call_event_id=%s: %s",
                job_id, call_event_id, exc,
            )
            create_exception(
                session,
                type="lead_state_update_failed",
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
