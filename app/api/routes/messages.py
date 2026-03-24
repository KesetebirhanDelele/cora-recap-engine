"""
Inbound message ingestion endpoint.

POST /v1/messages/inbound

Called by SMS/email providers when a contact replies. This endpoint:
  1. Inserts a row into inbound_messages
  2. Updates lead_state.last_replied_at = now
  3. Cancels all pending scheduled jobs for the contact

This is the authoritative reply-detection signal. Once this endpoint
is called, has_recent_reply() returns True and all future SMS/email
jobs for this contact are suppressed.

Authentication: none (called by provider webhook; operators should
restrict access via firewall/network rules in production).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.db import get_sync_session
from app.models.inbound_message import InboundMessage
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob

logger = logging.getLogger(__name__)

router = APIRouter()


class InboundMessageRequest(BaseModel):
    contact_id: str = Field(..., min_length=1)
    channel: Literal["sms", "email"]
    body: str = Field(..., min_length=1)


class InboundMessageResponse(BaseModel):
    id: str
    contact_id: str
    channel: str
    jobs_cancelled: int


@router.post("/inbound", response_model=InboundMessageResponse, status_code=201)
def ingest_inbound_message(payload: InboundMessageRequest) -> InboundMessageResponse:
    """
    Ingest an inbound SMS or email reply from a contact.

    Side effects (all in one transaction):
      - Inserts into inbound_messages
      - Updates lead_state.last_replied_at
      - Cancels all pending/claimed/running jobs for the contact
    """
    now = datetime.now(tz=timezone.utc)
    message_id = str(uuid.uuid4())

    with get_sync_session() as session:
        # 1. Insert inbound message record
        msg = InboundMessage(
            id=message_id,
            contact_id=payload.contact_id,
            channel=payload.channel,
            body=payload.body,
            received_at=now,
        )
        session.add(msg)
        session.flush()

        # 2. Update lead_state.last_replied_at (non-fatal if row missing)
        lead = session.scalars(
            select(LeadState).where(LeadState.contact_id == payload.contact_id)
        ).first()

        if lead is not None:
            session.execute(
                update(LeadState)
                .where(LeadState.id == lead.id)
                .values(last_replied_at=now, updated_at=now)
            )
            session.flush()
        else:
            logger.warning(
                "ingest_inbound_message: no lead_state for contact_id=%s — "
                "inbound message recorded but last_replied_at not set",
                payload.contact_id,
            )

        # 3. Cancel all pending jobs for this contact
        result = session.execute(
            update(ScheduledJob)
            .where(
                ScheduledJob.payload_json["contact_id"].as_string() == payload.contact_id,
                ScheduledJob.status.in_(["pending", "claimed", "running"]),
            )
            .values(status="cancelled", updated_at=now)
        )
        session.flush()
        jobs_cancelled = result.rowcount

    logger.info(
        "ingest_inbound_message | contact_id=%s channel=%s jobs_cancelled=%d",
        payload.contact_id, payload.channel, jobs_cancelled,
    )

    return InboundMessageResponse(
        id=message_id,
        contact_id=payload.contact_id,
        channel=payload.channel,
        jobs_cancelled=jobs_cancelled,
    )
