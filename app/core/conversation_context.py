"""
Conversation context builder.

Assembles a structured context object for AI message generation.
Includes recent call transcripts, lead state, outbound message history,
and inbound replies — everything needed to generate a personalised
follow-up SMS or email without hallucinating program details.

Transcript limit: 5 most recent call events (newest first).
Message history limit: last 10 outbound messages.
Reply history limit: last 10 inbound replies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_MAX_TRANSCRIPTS = 5
_MAX_OUTBOUND = 10
_MAX_INBOUND = 10


@dataclass
class ConversationContext:
    contact_id: str
    campaign_name: Optional[str]
    status: Optional[str]          # active | nurture | cold | closed | ...
    tier: Optional[str]            # None | '0' | '1' | '2' | '3'
    preferred_channel: Optional[str]
    transcripts: list[str] = field(default_factory=list)        # newest first
    outbound_messages: list[dict] = field(default_factory=list) # channel/body/subject
    inbound_replies: list[dict] = field(default_factory=list)   # channel/body


def get_conversation_context(
    session: Session, contact_id: str
) -> ConversationContext:
    """
    Build a ConversationContext for a contact.

    Never raises — returns a minimal context if queries fail.
    """
    from sqlalchemy import select

    ctx = ConversationContext(
        contact_id=contact_id,
        campaign_name=None,
        status=None,
        tier=None,
        preferred_channel=None,
    )

    try:
        from app.models.call_event import CallEvent
        from app.models.inbound_message import InboundMessage
        from app.models.lead_state import LeadState
        from app.models.outbound_message import OutboundMessage

        # ── Lead state ────────────────────────────────────────────────────────
        lead = session.scalars(
            select(LeadState).where(LeadState.contact_id == contact_id)
        ).first()
        if lead:
            ctx.campaign_name = lead.campaign_name
            ctx.status = lead.status
            ctx.tier = lead.ai_campaign_value
            ctx.preferred_channel = lead.preferred_channel

        # ── Call transcripts (newest first) ───────────────────────────────────
        events = session.scalars(
            select(CallEvent)
            .where(
                CallEvent.contact_id == contact_id,
                CallEvent.transcript.isnot(None),
            )
            .order_by(CallEvent.created_at.desc())
            .limit(_MAX_TRANSCRIPTS)
        ).all()
        ctx.transcripts = [e.transcript for e in events if e.transcript]

        # ── Outbound message history ──────────────────────────────────────────
        outbound = session.scalars(
            select(OutboundMessage)
            .where(OutboundMessage.contact_id == contact_id)
            .order_by(OutboundMessage.created_at.desc())
            .limit(_MAX_OUTBOUND)
        ).all()
        ctx.outbound_messages = [
            {"channel": m.channel, "body": m.body, "subject": m.subject}
            for m in outbound
        ]

        # ── Inbound replies ───────────────────────────────────────────────────
        inbound = session.scalars(
            select(InboundMessage)
            .where(InboundMessage.contact_id == contact_id)
            .order_by(InboundMessage.received_at.desc())
            .limit(_MAX_INBOUND)
        ).all()
        ctx.inbound_replies = [
            {"channel": m.channel, "body": m.body}
            for m in inbound
        ]

    except Exception as exc:
        logger.error(
            "get_conversation_context: error | contact_id=%s: %s",
            contact_id, exc,
        )

    return ctx
