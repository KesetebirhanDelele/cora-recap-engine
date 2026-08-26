"""
Conversation context builder.

Assembles a structured context object for AI message generation.
Includes recent call transcripts, lead state, outbound message history, and
optionally the full two-way GHL conversation thread (SMS + email replies).

GHL conversation history is fetched when settings.ghl_fetch_conversation_history
is True.  The lookup uses the lead's phone number (phone_number_to, regardless of
call direction — see spec/24: phone_number_from is Synthflow's own agent line,
not the lead's number, on both inbound and outbound calls) to find the GHL
contact, then fetches the most recent conversation and its messages.  The fetch
is non-fatal — any GHL error leaves ghl_messages empty so generation proceeds
without history.

Transcript limit: 5 most recent call events (newest first).
Outbound message limit: last 10 outbound messages.
GHL message limit: settings.ghl_conversation_history_limit (default 15).
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


@dataclass
class ConversationContext:
    contact_id: str
    campaign_name: Optional[str]
    status: Optional[str]          # active | nurture | cold | closed | ...
    tier: Optional[str]            # None | '0' | '1' | '2' | '3'
    preferred_channel: Optional[str]
    lead_first_name: Optional[str] = None   # from raw_payload_json['Name']
    attempt_number: int = 1                 # 1-based voicemail tier attempt
    transcripts: list[str] = field(default_factory=list)        # newest first
    outbound_messages: list[dict] = field(default_factory=list) # channel/body/subject
    ghl_messages: list[dict] = field(default_factory=list)      # direction/type/body/date from GHL


def get_conversation_context(
    session: Session,
    contact_id: str,
    attempt_number: int = 1,
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
        attempt_number=attempt_number,
    )

    try:
        from app.models.call_event import CallEvent
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

        # ── Lead first name from most recent call_event payload ───────────────
        # raw_payload_json['Name'] is set by Synthflow from the GHL contact.
        # Use the most recent event regardless of transcript availability.
        most_recent_event = session.scalars(
            select(CallEvent)
            .where(CallEvent.contact_id == contact_id)
            .order_by(CallEvent.created_at.desc())
            .limit(1)
        ).first()
        if most_recent_event and most_recent_event.raw_payload_json:
            raw_name = most_recent_event.raw_payload_json.get("Name", "")
            # Take first word only (first name), strip whitespace
            first = (raw_name or "").strip().split()[0] if raw_name and raw_name.strip() else ""
            ctx.lead_first_name = first or None

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

        # ── GHL conversation history (opt-in, non-fatal) ──────────────────────
        # Requires settings.ghl_fetch_conversation_history = True.
        # Uses the lead's phone from the most recent call_event to locate the
        # GHL contact and pull the active conversation thread.
        from app.config import get_settings
        settings = get_settings()
        if settings.ghl_fetch_conversation_history:
            ctx.ghl_messages = _fetch_ghl_messages(
                session=session,
                contact_id=contact_id,
                limit=settings.ghl_conversation_history_limit,
            )

    except Exception as exc:
        logger.error(
            "get_conversation_context: error | contact_id=%s: %s",
            contact_id, exc,
        )

    return ctx


def _fetch_ghl_messages(
    session,
    contact_id: str,
    limit: int = 15,
) -> list[dict]:
    """
    Fetch the lead's GHL conversation history using phone number lookup.

    Flow:
      1. Resolve the lead's phone from the most recent call_event:
         phone_number_to, regardless of direction — phone_number_from is
         Synthflow's own agent line, not the lead's number (spec/24).
      2. Search GHL for a contact matching that phone → get GHL contact_id.
      3. Fetch the most recent conversation for that contact.
      4. Return up to `limit` messages, normalised to
         {direction, type, body, date}.

    Returns an empty list on any failure — never raises.
    Skips Activity/System messages that carry no meaningful text.
    """
    from sqlalchemy import select

    from app.adapters.ghl import GHLClient, GHLError
    from app.models.call_event import CallEvent

    try:
        # ── Step 1: resolve lead phone from most recent call_event ────────────
        most_recent = session.scalars(
            select(CallEvent)
            .where(CallEvent.contact_id == contact_id)
            .order_by(CallEvent.created_at.desc())
            .limit(1)
        ).first()

        if not most_recent or not most_recent.raw_payload_json:
            logger.debug(
                "_fetch_ghl_messages: no call_event for contact_id=%s — skipping",
                contact_id,
            )
            return []

        payload = most_recent.raw_payload_json
        phone = payload.get("phone_number_to") or payload.get("phone_number_from")

        if not phone:
            logger.debug(
                "_fetch_ghl_messages: no phone resolved | contact_id=%s",
                contact_id,
            )
            return []

        # ── Step 2: GHL contact lookup by phone ───────────────────────────────
        client = GHLClient()
        ghl_contact = client.search_contact_by_phone(phone)
        if not ghl_contact:
            logger.info(
                "_fetch_ghl_messages: no GHL contact for phone=<redacted> contact_id=%s",
                contact_id,
            )
            return []

        ghl_contact_id = ghl_contact.get("id") or ghl_contact.get("contactId")
        if not ghl_contact_id:
            return []

        # ── Step 3: find most recent conversation ─────────────────────────────
        conversations = client.get_conversations_by_contact(ghl_contact_id, limit=5)
        if not conversations:
            logger.info(
                "_fetch_ghl_messages: no GHL conversations | contact_id=%s", contact_id,
            )
            return []

        # Conversations are returned newest-first; take the first one.
        conversation_id = conversations[0].get("id")
        if not conversation_id:
            return []

        # ── Step 4: fetch messages and normalise ──────────────────────────────
        raw_messages = client.get_conversation_messages(conversation_id, limit=limit)

        # Skip Activity/System messages — they carry no meaningful reply text.
        _SKIP_TYPES = {"Activity", "ActivityContact", "SystemGenerated", "Call"}

        normalised: list[dict] = []
        for msg in raw_messages:
            msg_type = msg.get("messageType") or msg.get("type") or ""
            if msg_type in _SKIP_TYPES:
                continue
            body = (msg.get("body") or msg.get("message") or "").strip()
            if not body:
                continue
            normalised.append({
                "direction": msg.get("direction", "outbound"),
                "type": msg_type or "SMS",
                "body": body,
                "date": msg.get("dateAdded") or msg.get("date", ""),
            })

        logger.info(
            "_fetch_ghl_messages: fetched %d messages | contact_id=%s conversation_id=%s",
            len(normalised), contact_id, conversation_id,
        )
        return normalised

    except GHLError as exc:
        logger.warning(
            "_fetch_ghl_messages: GHL error (non-fatal) | contact_id=%s: %s",
            contact_id, exc,
        )
        return []
    except Exception as exc:
        logger.warning(
            "_fetch_ghl_messages: unexpected error (non-fatal) | contact_id=%s: %s",
            contact_id, exc,
        )
        return []
