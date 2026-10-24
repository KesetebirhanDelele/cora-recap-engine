"""
Failed-transfer recovery - pure, no I/O (spec/39).

A caller asked for a human, the live transfer did not connect (carrier block, outside the staff member's availability), and
the voice agent did not book a callback (2026-10-23: a Cold Lead caller asked for a human five times and was told goodbye;
the engine had parked the lead as "human_transfer" and created no staff task). This module decides, from the call's
`executed_actions` and detected intent, whether a staff callback task must exist, and builds it.

Rule: a human was requested (a transfer action failed, or intent human_transfer_request) AND no transfer action connected AND no callback was booked -> a task is due NOW for the
staff member the caller asked for (the transfer action's name says who: admissions / IPBC-payment / support).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.intent_detection import _extract_executed_actions

HUMAN_REQUEST_INTENT = "human_transfer_request"


def needs_callback(detected_intent: str | None, executed_actions: Any) -> bool:
    """True when a human was asked for, no transfer connected, and no callback was booked on the call.

    A transfer action that failed is itself proof a human was requested (so this does not depend on the intent already being
    stored); the intent covers a human request where the agent never tried a transfer."""
    flags = _extract_executed_actions(executed_actions)
    if flags["transfer_succeeded"] or flags["booking_success"]:
        return False
    return flags["transfer_failed"] or (detected_intent or "") == HUMAN_REQUEST_INTENT


def target_kind(executed_actions: Any) -> str:
    """Which team the failed transfer was aimed at: 'admissions', 'ipbc' or 'support' (default 'admissions')."""
    name = (_extract_executed_actions(executed_actions)["transfer_target"] or "").lower()
    if "taiwo" in name or "ipbc" in name or "payment" in name:
        return "ipbc"
    if any(w in name for w in ("support", "bala", "farhat")):
        return "support"
    return "admissions"


def build_task(lead_name: str, phone: str, executed_actions: Any, *, now: datetime | None = None) -> dict[str, str]:
    """Title / description / due date for the staff callback. Never contains a secret; the phone is the lead's own."""
    flags = _extract_executed_actions(executed_actions)
    now = now or datetime.now(tz=timezone.utc)
    who = (lead_name or "").strip() or "the caller"
    reason = flags["transfer_reason"] or ("transfer not attempted" if not flags["transfer_attempted"] else "transfer did not connect")
    title = f"CALL BACK NOW: {who} asked for a human and was not connected"
    description = (
        f"{who} ({phone or 'phone not on file'}) asked the voice agent to speak with a person. The live transfer did not "
        f"connect ({reason}) and no callback was booked on the call. Please call them back today, and check the call "
        f"transcript for what they asked (pricing / program details). Created automatically by Cora (failed-transfer recovery)."
    )
    return {"title": title, "description": description, "due_date": now.isoformat()}
