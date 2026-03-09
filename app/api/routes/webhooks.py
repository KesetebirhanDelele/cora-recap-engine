"""
POST /v1/webhooks/calls — inbound and outbound call event intake.

Phase 1: route stub.  Payload validation and routing logic added in Phase 2+.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/calls", status_code=status.HTTP_202_ACCEPTED)
async def receive_call_event(request: Request) -> dict[str, Any]:
    """
    Accept a call event payload.

    Expected fields: call_id, direction, status, start_time, duration_seconds,
    phones (caller/callee), and optional campaign metadata.

    Phase 1: returns 202 Accepted with placeholder.  Real routing wired in Phase 2.
    """
    body = await request.json()
    call_id = body.get("call_id")

    if not call_id:
        logger.warning("Received call event with no call_id — will create exception record")
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

    logger.info("Received call event | call_id=%s [stub — routing not yet implemented]", call_id)

    return {"status": "accepted", "call_id": call_id}
