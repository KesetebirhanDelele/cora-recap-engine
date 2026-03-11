"""
POST /v1/webhooks/calls — inbound and outbound call event intake.

Accepts Synthflow post-call webhook payloads and enqueues a
process_call_event job for the worker to process.

Routing is direction-agnostic: both inbound and outbound calls are
accepted on the same endpoint. The worker reads the `direction` and
`status` fields from the payload to determine the processing path.

Durability contract:
  - The ScheduledJob row is written to Postgres before returning 202.
  - If the RQ queue is available (app.state.default_queue), the job is
    also enqueued immediately for real-time processing.
  - If Redis is unavailable, the job stays pending in Postgres and the
    worker recovery loop picks it up on next startup.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.db import get_sync_session
from app.worker.jobs.call_processing import process_call_event
from app.worker.scheduler import schedule_job

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/calls", status_code=status.HTTP_202_ACCEPTED)
async def receive_call_event(request: Request) -> dict[str, Any]:
    """
    Accept a call event payload and enqueue a process_call_event job.

    Expected fields: call_id, direction, status, start_time,
    duration_seconds, phones (caller/callee), and optional campaign
    metadata. The full payload is stored in the job for the worker.

    Returns 202 with call_id and job_id on success.
    Returns 422 if call_id is missing.
    """
    body = await request.json()
    call_id = body.get("call_id")

    if not call_id:
        logger.warning("Received call event with no call_id")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "missing_call_id",
                    "message": "call_id is required",
                    "retryable": False,
                }
            },
        )

    rq_queue = getattr(request.app.state, "default_queue", None)

    with get_sync_session() as session:
        job = schedule_job(
            session=session,
            job_type="process_call_event",
            entity_type="call",
            entity_id=call_id,
            run_at=datetime.now(tz=timezone.utc),
            payload=body,
            rq_queue=rq_queue,
            rq_job_func=process_call_event if rq_queue is not None else None,
        )

    logger.info(
        "Received call event | call_id=%s job_id=%s enqueued=%s",
        call_id, job.id, rq_queue is not None,
    )

    return {"status": "accepted", "call_id": call_id, "job_id": job.id}
