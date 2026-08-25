"""
Pre-call escalation guard.

Problem this closes: a lead can escalate to "talk to a human" or get a
callback/appointment booked during one call (captured as call_events
detected_intent), and minutes later get auto-dialed again by an unrelated
outbound campaign trigger (e.g. a re-fired GHL New Lead/Cold Lead webhook)
that has no awareness the escalation happened. Neither enter_campaign() nor
launch_outbound_call_job() checked for this before this guard existed.
See PROGRESS.md 2026-08-24 for the incident (call b683049e-... / 1e4df8b1-...)
this was found from.

Guard logic:
  Blocks outbound dialing when the most recent call_events row for a contact,
  within the last _URGENT_WINDOW_DAYS days, has a detected_intent in
  _URGENT_INTENTS AND lead_state.sales_outcome has not since been set. Once a
  sales rep records any outcome for the lead via the Sales Queue, triage is
  considered done and normal campaign cadence resumes.

_URGENT_INTENTS is deliberately narrower than dashboard_metrics._INTENT_SCORES'
"urgent" tier (score >= 80): that list also includes intents like re_engaged,
which mean "call this hot lead more," not "stop calling — a human already
took over." Suppression semantics and sales-priority-ranking semantics differ.

This guard reads only data already collected by this app (call_events,
lead_state) — it does not depend on any GHL-side signal ingestion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

_URGENT_WINDOW_DAYS = 30

_URGENT_INTENTS = frozenset({
    "human_transfer_request",
    "callback_request",
    "callback_with_time",
    "enrolled",
})
_URGENT_INTENTS_SQL = ",".join(f"'{intent}'" for intent in sorted(_URGENT_INTENTS))


def check_urgent_unresolved(session: Session, contact_id: str) -> Optional[dict[str, Any]]:
    """
    Return escalation details if this contact has an unresolved urgent signal
    within the last _URGENT_WINDOW_DAYS days, else None.

    Callers should hold outbound dialing when this returns non-None.
    """
    if not contact_id:
        return None

    window_start = datetime.now(tz=timezone.utc) - timedelta(days=_URGENT_WINDOW_DAYS)

    row = session.execute(text(f"""
        SELECT
            ce.id                                          AS call_event_id,
            ce.call_id,
            ce.detected_intent,
            COALESCE(ce.start_time_utc, ce.created_at)      AS call_time,
            ls.sales_outcome
        FROM call_events ce
        LEFT JOIN lead_state ls ON ls.contact_id = ce.contact_id
        WHERE ce.contact_id = :contact_id
          AND ce.detected_intent IN ({_URGENT_INTENTS_SQL})
          AND COALESCE(ce.start_time_utc, ce.created_at) >= :window_start
        ORDER BY COALESCE(ce.start_time_utc, ce.created_at) DESC
        LIMIT 1
    """), {"contact_id": contact_id, "window_start": window_start}).fetchone()

    if row is None:
        return None

    if row[4]:  # sales_outcome already recorded — a rep has triaged this lead
        return None

    call_time = row[3]
    return {
        "call_event_id": row[0],
        "call_id": row[1],
        "detected_intent": row[2],
        "call_time": call_time.isoformat() if hasattr(call_time, "isoformat") else call_time,
    }
