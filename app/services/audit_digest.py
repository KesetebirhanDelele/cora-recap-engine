"""
Daily audit digest sender (Ali approved 2026-11-11). Pure rules live in app/core/audit_digest.py.

Runs every metrics cycle (60 s) after 8 AM Central and is idempotent: one email per Central day, claimed first with an app_config
ledger row, so concurrent workers and restarts never send twice. A failed send releases the claim and is retried on the next cycle
(up to MAX_ATTEMPTS); then Kes gets a dashboard exception and an email, so a missing digest is never silent.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import audit_digest as rule
from app.services import open_house_reminder as sender

logger = logging.getLogger(__name__)

RECIPIENTS_KEY = "audit_digest_to"      # optional comma-separated override
ENABLED_KEY = "audit_digest_enabled"    # "false" switches the digest off
NOTICE = {"subject": "Cora could not send the daily audit digest",
          "tail": "The daily audit digest did not go out to the people listed. Cleared messages are still logged in the database (kind content_ok)."}


def recipients(settings: Any, override: str | None = None) -> list[str]:
    """Kes (alert_email_to) plus Ali, no duplicates, unless audit_digest_to is set."""
    raw = override if override else ",".join([getattr(settings, "alert_email_to", None) or "", rule.ALI])
    out: list[str] = []
    for a in raw.split(","):
        a = a.strip()
        if a and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def _already_sent(session: Session, key: str) -> bool:
    return session.execute(text("SELECT 1 FROM app_config WHERE key = :k"), {"k": key}).first() is not None


def cleared_items(session: Session, day) -> list[dict]:
    """Messages and calls the AI meaning check cleared on that Central day (by when the check ran), oldest first."""
    start, end = rule.window(day)
    rows = session.execute(text(
        "SELECT checked_at, channel, contact_id, detail FROM channel_events "
        "WHERE kind = 'content_ok' AND checked_at >= :s AND checked_at < :e ORDER BY checked_at"), {"s": start, "e": end}).fetchall()
    items = []
    for at, channel, contact_id, detail in rows:
        d = detail if isinstance(detail, dict) else {}
        items.append({"at": at, "channel": channel, "contact_id": contact_id, "terms": d.get("terms"), "votes": d.get("votes"),
                      "reason": d.get("reason"), "text": d.get("snippet") or d.get("excerpt")})
    return items


def run_audit_digest(session: Session, settings: Any, now: datetime) -> dict[str, Any]:
    """Send yesterday's digest once. Returns what was done."""
    from app.core.app_config import get_str
    from app.services.alerting import _smtp_send

    done: dict[str, Any] = {"sent": None}
    if get_str(ENABLED_KEY, session, settings, "true").lower() not in ("true", "1", "yes"):
        return done
    day = rule.digest_day(now)
    if day is None:
        return done
    key = f"audit_digest:{day.isoformat()}"
    if _already_sent(session, key):                      # cheap check first: this runs every minute
        return done
    subject, body = rule.build_digest(day, cleared_items(session, day))
    to = recipients(settings, get_str(RECIPIENTS_KEY, session, settings, ""))
    if sender._send_once(session, settings, key, f"Audit digest for {day.isoformat()}", to,
                         lambda addrs: _smtp_send(settings, addrs, subject, body, log_label="audit_digest", raise_on_failure=True),
                         now, notice=NOTICE):
        done["sent"] = day.isoformat()
    return done
