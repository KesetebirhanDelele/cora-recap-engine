"""
Lead lifecycle state machine.

Single source of truth for all lead status transitions.  Enforces terminal
states, directional rules, and no-regression constraints so callers cannot
accidentally demote a lead or reopen a suppressed contact.

States
------
  None / "active"  — initial; being actively contacted
  "nurture"        — warm but not ready; scheduled for follow-up later
  "cold"           — completed nurture timeout; in cold outreach campaign
  "do_not_call"    — terminal; contact requested no more calls
  "invalid"        — terminal; wrong number or bad contact data
  "closed"         — terminal; explicitly not interested

Events
------
  opt_out            → do_not_call  (any non-terminal state)
  wrong_number       → invalid      (any non-terminal state)
  not_interested     → closed       (any non-terminal state)
  interested_not_now → nurture      (active / None / cold only)
  uncertain          → nurture      (active / None / cold only)
  timeout            → cold         (nurture only)
  reactivate         → active       (cold / nurture only)

Rules
-----
  • Terminal states block all further transitions (logged and skipped).
  • opt_out / wrong_number / not_interested override any non-terminal state.
  • "interested_not_now" / "uncertain" cannot upgrade a closed/DNC lead.
  • "cold" is NOT terminal — a warm signal can re-enter nurture.
  • "reactivate" moves a cold/nurture lead back into active outreach.
  • Optimistic concurrency: conflicts are logged and return None (no exception).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TERMINAL_STATES: frozenset[str] = frozenset({"do_not_call", "invalid", "closed"})

# Transition table:
#   event → (allowed_from | None, to_state)
# allowed_from=None means any non-terminal state is permitted.
_TRANSITIONS: dict[str, tuple[frozenset[str | None] | None, str]] = {
    "opt_out":            (None,                                "do_not_call"),
    "wrong_number":       (None,                                "invalid"),
    "not_interested":     (None,                                "closed"),
    "interested_not_now": (frozenset({None, "active", "cold"}), "nurture"),
    "uncertain":          (frozenset({None, "active", "cold"}), "nurture"),
    "timeout":            (frozenset({"nurture"}),              "cold"),
    "reactivate":         (frozenset({"cold", "nurture"}),      "active"),
}


def transition_lead_state(session: Session, lead: Any, event: str) -> str | None:
    """
    Apply event to a lead and persist the resulting status.

    Returns the new status string on success.
    Returns None when:
      - the lead is already in a terminal state
      - the event is not applicable from the current state
      - the event is unknown
      - an optimistic-concurrency conflict was detected (another worker won)

    Callers should treat None as "no-op" — no retry is needed.
    """
    from sqlalchemy import update

    from app.models.lead_state import LeadState

    current = lead.status  # None | "active" | "nurture" | "cold" | terminal

    if current in TERMINAL_STATES:
        logger.info(
            "lifecycle: blocked — terminal state | contact_id=%s status=%r event=%r",
            lead.contact_id, current, event,
        )
        return None

    rule = _TRANSITIONS.get(event)
    if rule is None:
        logger.warning(
            "lifecycle: unknown event %r | contact_id=%s", event, lead.contact_id
        )
        return None

    allowed_from, to_state = rule

    if allowed_from is not None and current not in allowed_from:
        logger.info(
            "lifecycle: transition not applicable | contact_id=%s event=%r "
            "from=%r allowed_from=%s",
            lead.contact_id, event, current, allowed_from,
        )
        return None

    old_status = current
    now = datetime.now(tz=timezone.utc)

    result = session.execute(
        update(LeadState)
        .where(LeadState.id == lead.id, LeadState.version == lead.version)
        .values(status=to_state, version=lead.version + 1, updated_at=now)
    )
    session.flush()

    if result.rowcount == 0:
        logger.warning(
            "lifecycle: version conflict — transition skipped | contact_id=%s event=%r",
            lead.contact_id, event,
        )
        return None

    session.refresh(lead)

    logger.info(
        "lifecycle: transitioned | contact_id=%s event=%r %r → %r",
        lead.contact_id, event, old_status, to_state,
        extra={},  # structured log placeholder
    )
    return to_state
