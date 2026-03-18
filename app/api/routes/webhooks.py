"""
POST /v1/webhooks/calls — inbound and outbound call event intake.

Accepts Synthflow post-call webhook payloads and enqueues a
process_call_event job for the worker to process.

Routing is direction-agnostic: both inbound and outbound calls are
accepted on the same endpoint. The worker reads the `call_status` (or
`status`) and `end_call_reason` fields to determine the processing path.

Durability contract:
  - The ScheduledJob row is written to Postgres before returning 202.
  - If the RQ queue is available (app.state.default_queue), the job is
    also enqueued immediately for real-time processing.
  - If Redis is unavailable, the job stays pending in Postgres and the
    worker recovery loop picks it up on next startup.

Payload normalization:
  Synthflow field names do not always match internal schema.
  normalize_synthflow_payload() is applied before any routing logic.
  Normalization is additive — original fields are preserved alongside
  the normalized keys, so the stored payload is always the full raw
  provider payload plus normalized aliases.
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

# ── Synthflow field aliases ───────────────────────────────────────────────────
# Maps Synthflow provider field names → internal field names.
# Only applied when the internal field is absent.
# Add new aliases here as new Synthflow payload shapes are observed.
_CALL_ID_ALIASES = ("Call_id", "callId", "call_id")  # priority order
_DURATION_ALIASES = ("duration", "duration_seconds")
_STATUS_ALIASES = ("call_status", "status")


def normalize_synthflow_payload(body: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a Synthflow completed-call payload into internal field names.

    Additive: adds normalized keys alongside the original provider keys.
    The caller receives a merged dict — original fields are never removed.
    This preserves the full raw provider payload for audit storage while
    making downstream routing logic field-name agnostic.

    Normalizations applied:
      call_id         — accepts Call_id, callId (Synthflow variants observed)
      duration_seconds — accepts duration (Synthflow omits the _seconds suffix)
      call_status     — accepted as-is; status is the internal fallback
      direction       — defaults to 'outbound' for Synthflow-originated events
    """
    # Work on a shallow copy so we never mutate the caller's dict
    payload = dict(body)

    # ── call_id: case-insensitive resolution ──────────────────────────────────
    if not payload.get("call_id"):
        for alias in _CALL_ID_ALIASES:
            if alias in payload and payload[alias]:
                payload["call_id"] = payload[alias]
                if alias != "call_id":
                    logger.debug(
                        "normalize_synthflow_payload: mapped %r → call_id", alias
                    )
                break

    # ── duration_seconds: alias from Synthflow's 'duration' ──────────────────
    if not payload.get("duration_seconds") and payload.get("duration") is not None:
        payload["duration_seconds"] = payload["duration"]

    # ── direction: Synthflow outbound calls may omit this field ──────────────
    if not payload.get("direction"):
        payload["direction"] = "outbound"

    # ── phones: extract callee/caller from nested phones object ──────────────
    # Some Synthflow webhook variants nest phone numbers under {"phones": {"callee": ..., "caller": ...}}
    # instead of top-level phone_number_to / phone_number_from fields.
    if "phones" in payload and isinstance(payload["phones"], dict):
        phones = payload["phones"]
        if not payload.get("phone_number") and phones.get("callee"):
            payload["phone_number"] = phones["callee"]
        if not payload.get("phone_number_from") and phones.get("caller"):
            payload["phone_number_from"] = phones["caller"]
        logger.debug(
            "normalize_synthflow_payload: extracted phones.callee=%r phones.caller=%r",
            phones.get("callee"), phones.get("caller"),
        )

    # ── contact_id: fall back to any available phone field ───────────────────
    # Synthflow webhooks do not always include a GHL contact_id.
    # Use the first available phone field as a stable key for lead_state lookup /
    # creation until a real GHL contact_id is synced from GHL.
    if not payload.get("contact_id"):
        derived = (
            payload.get("phone_number_to")
            or payload.get("phone")
            or payload.get("phone_number")
        )
        if derived:
            payload["contact_id"] = derived
            logger.debug(
                "normalize_synthflow_payload: derived contact_id from phone field=%r",
                payload["contact_id"],
            )

    # ── campaign_name: default to 'New Lead' when absent ─────────────────────
    # Synthflow webhook payloads may not include campaign_name.
    # Defaulting ensures process_voicemail_tier can look up a valid tier policy.
    if not payload.get("campaign_name"):
        payload["campaign_name"] = "New Lead"
        logger.debug("normalize_synthflow_payload: defaulted campaign_name to 'New Lead'")

    return payload


@router.post("/calls", status_code=status.HTTP_202_ACCEPTED)
async def receive_call_event(request: Request) -> dict[str, Any]:
    """
    Accept a Synthflow call event payload and enqueue a process_call_event job.

    Normalizes provider field names before validation so Synthflow payloads
    (which use Call_id, duration, etc.) are accepted alongside internal format.

    Returns 202 with call_id and job_id on success.
    Returns 422 if call_id is absent even after normalization.
    """
    raw_body = await request.json()
    body = normalize_synthflow_payload(raw_body)
    call_id = body.get("call_id")

    if not call_id:
        logger.warning(
            "Received call event with no call_id | keys=%s",
            list(raw_body.keys()),
        )
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
            payload=body,  # normalized payload stored; original fields preserved
            rq_queue=rq_queue,
            rq_job_func=process_call_event if rq_queue is not None else None,
        )
        # Capture ID inside the session — ORM objects detach after session closes
        job_id = job.id

    logger.info(
        "Received call event | call_id=%s job_id=%s enqueued=%s",
        call_id, job_id, rq_queue is not None,
    )

    return {"status": "accepted", "call_id": call_id, "job_id": job_id}
