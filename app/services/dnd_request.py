"""A lead on DND (or with an opt-out tag) asked to be contacted: tell a person (Kes 2026-10-09).

Cora never overrides DND by itself: a stop request is a legal one and a later "call me" may not be meant as new permission. So the
send is still blocked, but one alert goes out (dashboard + email, Ali copied) with the lead's name, phone, email and exact words.
A person confirms with the lead, switches DND off in GHL, and clears Cora's do-not-call flag. One alert per request (deduped by key).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

ALERT_PREFIX = "dnd_contact_request:"
CALLBACK_REASONS = frozenset({"callback_request", "callback_with_time", "call_later_no_time"})
STEPS = ("Next: confirm with the lead that they want to be contacted, then in GHL switch off the DND for that channel and remove any "
         "do-not-contact tag, and ask Claude to clear Cora's do-not-call flag. Cora has NOT contacted them.")


def notify(session: Session, settings: Any, *, key: str, contact_id: str, how: str, words: str, why_blocked: str) -> bool:
    """Create the alert and email it, once per `key`. Returns True when sent now. Never raises."""
    try:
        from app.models.alert_event import AlertEvent
        from app.services.alerting import _send_alert_email
        from app.services.channel_health import _lead_line

        alert_type = f"{ALERT_PREFIX}{key}"
        if session.execute(text("SELECT 1 FROM alert_events WHERE alert_type = :t LIMIT 1"), {"t": alert_type}).fetchone():
            return False
        now = datetime.now(tz=timezone.utc)
        msg = (f"A lead who opted out asked to be contacted ({how}).\n"
               f"Lead: {_lead_line(settings, contact_id)}\n"
               f"They said: \"{(words or '').strip()[:400]}\"\n"
               f"Cora blocked the contact because: {why_blocked}.\n{STEPS}")
        aid = str(uuid.uuid4())
        session.add(AlertEvent(id=aid, alert_type=alert_type, severity="warning", status="active", message=msg,
                               email_sent_at=now, last_seen_at=now, created_at=now))
        session.flush()
        _send_alert_email(settings=settings, alert_id=aid, alert_type=alert_type, severity="warning", message=msg, now=now)
        return True
    except Exception as exc:
        logger.warning("dnd_request: alert failed for %s: %s", contact_id, exc)
        return False


def last_words(session: Session, contact_id: str) -> str:
    """What the lead said on their most recent call (their lines only, last 400 chars)."""
    from app.core import optout as oo

    try:
        row = session.execute(text(
            "SELECT transcript FROM call_events WHERE contact_id = :c AND transcript IS NOT NULL ORDER BY created_at DESC LIMIT 1"),
            {"c": contact_id}).fetchone()
        return oo.human_lines(row[0])[-400:] if row else ""
    except Exception:
        return ""


def on_blocked_callback(session: Session, settings: Any, job: Any, contact_id: str, why_blocked: str) -> None:
    """Call gate hook: a callback the lead asked for was blocked by their DND."""
    reason = (getattr(job, "payload_json", None) or {}).get("intent_reason")
    if reason in CALLBACK_REASONS:
        notify(session, settings, key=f"job:{job.id}", contact_id=contact_id, how=f"asked for a call back, {reason.replace('_', ' ')}",
               words=last_words(session, contact_id), why_blocked=why_blocked)


def on_reply(session: Session, settings: Any, *, channel: str, contact_id: str, message_id: str, body: str) -> None:
    """Reply hook: a text or email asking to be called or sent information; alert only when the lead has DND / an opt-out tag."""
    from app.core.contact_request import asks_to_be_contacted
    from app.core.sms_eligibility import ghl_dnd_reason
    from app.services import optout

    try:
        if not contact_id or not asks_to_be_contacted(body, channel):
            return
        record = optout._ghl(settings).get_contact(contact_id)
        c = record.get("contact", record)
        dnd = optout.dnd_channels({"contact": c})
        why = f"GHL DND is on for {', '.join(sorted(dnd))}" if dnd else (ghl_dnd_reason(c, "sms") or ghl_dnd_reason(c, "email"))
        if why:
            notify(session, settings, key=f"msg:{message_id}", contact_id=contact_id, how=f"asked by {channel} to be contacted",
                   words=body, why_blocked=why)
    except Exception as exc:
        logger.warning("dnd_request: reply check failed for %s: %s", contact_id, exc)
