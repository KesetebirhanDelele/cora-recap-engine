"""
Reply detection — check whether a contact has sent an inbound reply.

Checks two independent signals (belt-and-suspenders):
  1. inbound_messages table has at least one row for the contact
  2. lead_state.last_replied_at is not null

Returns True if either signal is positive.

Performance:
  Both queries use indexed contact_id columns and short-circuit on the
  first result.  This is intentionally fast and non-blocking — callers
  use it as a gate before scheduling or sending messages.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def has_recent_reply(session: Session, contact_id: str) -> bool:
    """
    Return True if the contact has sent any inbound reply (SMS or email).

    Checks inbound_messages first (cheaper row existence check), then
    falls back to lead_state.last_replied_at.

    Returns False when contact_id is empty or on any DB error (fail-open:
    do not suppress messaging due to a detection failure).
    """
    if not contact_id:
        return False

    try:
        from sqlalchemy import select

        from app.models.inbound_message import InboundMessage
        from app.models.lead_state import LeadState

        # Signal 1: inbound_messages row exists
        inbound = session.scalars(
            select(InboundMessage)
            .where(InboundMessage.contact_id == contact_id)
            .limit(1)
        ).first()
        if inbound is not None:
            logger.info(
                "has_recent_reply: inbound message found | contact_id=%s", contact_id
            )
            return True

        # Signal 2: lead_state.last_replied_at is set
        lead = session.scalars(
            select(LeadState).where(LeadState.contact_id == contact_id)
        ).first()
        if lead is not None and lead.last_replied_at is not None:
            logger.info(
                "has_recent_reply: last_replied_at set | contact_id=%s last=%s",
                contact_id, lead.last_replied_at,
            )
            return True

    except Exception as exc:
        # Fail-open: a DB error must not suppress outbound messaging
        logger.error(
            "has_recent_reply: DB error, returning False | contact_id=%s: %s",
            contact_id, exc,
        )

    return False
