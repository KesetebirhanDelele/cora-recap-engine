"""
POST /v1/test/calls/outbound — dev/staging only test call launcher.

Allows an operator to trigger a real Synthflow outbound call through the
same path production will use, for end-to-end validation (spec 15).

SAFETY:
  - This route is only registered when APP_ENV != 'production'.
  - GHL writes remain shadow-gated unless explicitly enabled.
  - The Synthflow Make Call workflow is invoked; the result arrives via
    the existing POST /v1/webhooks/calls callback.

Operator flow:
  1. POST this endpoint with phone, lead_name, campaign_name.
  2. The app enqueues a launch_outbound_call job.
  3. The worker calls SynthflowClient.launch_new_lead_call().
  4. Synthflow calls the operator's phone.
  5. After the call, Synthflow POSTs the result to /v1/webhooks/calls.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, field_validator

from app.db import get_sync_session
from app.worker.jobs.outbound_jobs import launch_outbound_call_job
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

router = APIRouter()

_E164_PREFIX = "+"


class OutboundTestCallRequest(BaseModel):
    phone_number: str
    lead_name: str
    campaign_name: str = "New_Lead"
    notes: str = ""
    source: str = "e2e_test_harness"

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(_E164_PREFIX) or len(v) < 10:
            raise ValueError(
                "phone_number must be in E.164 format, e.g. +17865551234"
            )
        return v


@router.post("/outbound", status_code=status.HTTP_202_ACCEPTED)
async def launch_test_outbound_call(
    body: OutboundTestCallRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Launch a real outbound test call via Synthflow.

    Returns a correlation_id and job_id for tracking.
    The Synthflow agent will call the supplied phone number.
    Results arrive via POST /v1/webhooks/calls after the call ends.
    """
    correlation_id = str(uuid.uuid4())
    rq_queue = getattr(request.app.state, "default_queue", None)

    payload = {
        "phone_number": body.phone_number,
        "lead_name": body.lead_name,
        "campaign_name": body.campaign_name,
        "source": body.source,
        "notes": body.notes,
        "correlation_id": correlation_id,
    }

    with get_sync_session() as session:
        job = schedule_job(
            session=session,
            job_type="launch_outbound_call",
            entity_type="test_call",
            entity_id=correlation_id,
            run_at=datetime.now(tz=timezone.utc),
            payload=payload,
            rq_queue=rq_queue,
            rq_job_func=launch_outbound_call_job if rq_queue is not None else None,
        )
        # Capture ID inside the session — ORM objects detach after session closes
        job_id = job.id

    logger.info(
        "Test outbound call requested | correlation_id=%s job_id=%s phone=<redacted> "
        "campaign=%s enqueued=%s",
        correlation_id, job_id, body.campaign_name, rq_queue is not None,
    )

    return {
        "status": "accepted",
        "correlation_id": correlation_id,
        "job_id": job_id,
        "campaign_name": body.campaign_name,
        "note": (
            "The Synthflow agent will call the supplied phone number. "
            "Results arrive at POST /v1/webhooks/calls after the call ends."
        ),
    }


