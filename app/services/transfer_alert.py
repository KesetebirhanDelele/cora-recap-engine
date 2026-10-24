"""
Failed live-transfer alert (spec/39): one email per batch of NEW failed transfers, naming each caller.

A voice agent that cannot connect a caller who asked for a person is a customer-facing failure that used to be silent
(2026-10-23: five requests, a carrier-blocked transfer, no alert, no task). The check reads the last 24 h of calls, finds those
whose executed_actions show a transfer that failed and none that connected, and alerts once per call: the call id is written
into the alert message, so acknowledging or resolving an alert never makes the same call alert again. Active alerts resolve
themselves when no failed transfer is left in the 24 h window. Read-only on call data; the only write is the alert row.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import transfer_recovery as tr
from app.core.intent_detection import _extract_executed_actions

TRANSFER_ALERT = "call_transfer_failed"
WINDOW_HOURS = 24
MAX_LINES = 10

_REASON_HINT = {
    "transfer_blocked_by_carrier": "the carrier blocked the transfer number (Synthflow / telephony issue)",
    "transfer_outside_availability": "the staff member's availability window was closed",
    "transfer_failed": "the destination did not answer",
}


def failed_transfers(rows: list[Any]) -> list[dict[str, Any]]:
    """Calls (rows: call_id, lead_name, contact_id, campaign_name, start_time_utc, raw_payload_json) where a transfer failed and
    none connected. A call whose transfer failed but a retry connected is not a failure."""
    out = []
    for call_id, lead_name, contact_id, campaign, started, payload in rows:
        if isinstance(payload, (str, bytes)):                  # raw SQL returns JSON text on some drivers / SQLite
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}
        payload = payload if isinstance(payload, dict) else {}
        ea = payload.get("executed_actions")
        f = _extract_executed_actions(ea)
        if not f["transfer_failed"] or f["transfer_succeeded"]:
            continue
        out.append({
            "call_id": call_id, "name": (lead_name or "").strip() or "unknown caller",
            "phone": payload.get("phone_number_to") or payload.get("phone_number") or payload.get("phone") or contact_id or "",
            "campaign": campaign or "", "started": started, "target": f["transfer_target"], "kind": tr.target_kind(ea),
            "reason": f["transfer_reason"], "booked": bool(f["booking_success"]),
        })
    return out


def _line(c: dict[str, Any]) -> str:
    at = c["started"]
    when = at.strftime("%Y-%m-%d %H:%M UTC") if hasattr(at, "strftime") else str(at)
    hint = _REASON_HINT.get(c["reason"], "")
    return (f"- {c['name']} ({c['phone']}): {c['campaign'] or 'call'} at {when} asked for {c['kind']} ({c['target']}); "
            f"transfer failed: {c['reason']}{' - ' + hint if hint else ''}; "
            f"callback booked on the call: {'yes' if c['booked'] else 'NO'}; call id {c['call_id']}")


def run_transfer_failure_check(session: Session, settings: Any, now: datetime | None = None) -> int:
    """Alert on new failed transfers. Returns the number of failed transfers in the 24 h window."""
    from app.models.alert_event import AlertEvent
    from app.services.alerting import _send_alert_email

    now = now or datetime.now(tz=timezone.utc)
    rows = session.execute(text(
        "SELECT call_id, lead_name, contact_id, campaign_name, start_time_utc, raw_payload_json FROM call_events "
        "WHERE start_time_utc >= :s AND CAST(raw_payload_json AS TEXT) LIKE '%transfer_call_action_type%' "
        "ORDER BY start_time_utc"), {"s": now - timedelta(hours=WINDOW_HOURS)}).fetchall()
    failed = failed_transfers([tuple(r) for r in rows])
    total = len(failed)
    reported = " ".join(r[0] or "" for r in session.execute(
        text("SELECT message FROM alert_events WHERE alert_type = :t"), {"t": TRANSFER_ALERT}).fetchall())
    new = [c for c in failed if c["call_id"] and c["call_id"] not in reported]
    active = session.execute(
        text("SELECT id FROM alert_events WHERE alert_type = :t AND status = 'active' LIMIT 1"), {"t": TRANSFER_ALERT}).fetchone()
    if new:
        msg = (f"{len(new)} live call transfer(s) failed: the caller asked for a person and nobody was connected "
               f"({total} in the last {WINDOW_HOURS} h). The voice agent should have booked a callback on the call; a staff "
               f"callback task is created automatically when it did not. Check the transfer destination in Synthflow:\n"
               + "\n".join(_line(c) for c in new[:MAX_LINES]))
        aid = str(uuid.uuid4())
        session.add(AlertEvent(id=aid, alert_type=TRANSFER_ALERT, severity="warning", status="active", message=msg,
                               email_sent_at=now, last_seen_at=now, created_at=now))
        _send_alert_email(settings=settings, alert_id=aid, alert_type=TRANSFER_ALERT, severity="warning", message=msg, now=now)
    elif total and active:
        session.execute(text("UPDATE alert_events SET last_seen_at = :n WHERE id = :i"), {"n": now, "i": active[0]})
    elif not total and active:
        session.execute(text("UPDATE alert_events SET status = 'resolved', resolved_at = :n WHERE id = :i"),
                        {"n": now, "i": active[0]})
    return total
