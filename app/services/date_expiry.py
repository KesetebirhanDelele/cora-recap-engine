"""
Date expiry (spec/32).

Once the next-class-start / next-open-house date has PASSED, the dashboard setting must never keep telling a lead a past date.
Class start is cleared (set to ""). The Open House date ROLLS to the next one on the standing schedule (app/core/orientation.py,
Kes 2026-11-20) and its RSVP link is cleared, because Eventbrite is one page per event and the old page is dead; Kes, Ali and Jackie
get an email to set the new link. If no later Open House is on the schedule the date is cleared as before. Prompts
then switch to inviting the lead to start free at https://training.colaberry.com/start-free
(app/core/schedule_context.py) and the Wrong Date Monitor flags any dated
class / open-house mention sent after the clear.

Runs every metrics cycle; idempotent (a cleared setting is "unset" and is never
cleared twice), safe under concurrent workers (conditional UPDATE).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.orientation import OPEN_HOUSES, OPEN_HOUSE_TIME as OPEN_HOUSE_TIME_TEXT
from app.core.schedule_context import date_has_passed

logger = logging.getLogger(__name__)

EXPIRING_KEYS = {
    "next_class_start": "next class start",
    "next_open_house_date": "next Open House date",
}
UPDATED_BY = "auto_expire"


def expire_past_dates(session: Session, settings: Any, now: datetime) -> list[str]:
    """Clear every expired date setting. Returns the keys cleared."""
    from app.core.app_config import get_str

    tz = ZoneInfo(getattr(settings, "default_timezone", None) or "America/Chicago")
    today = now.astimezone(tz).date()
    cleared: list[str] = []

    for key, label in EXPIRING_KEYS.items():
        value = get_str(key, session, settings, "")
        if not date_has_passed(value, today):
            continue
        if key == OPEN_HOUSE_KEY:
            nxt = next_open_house(today)
            if nxt is not None:
                # Roll to the next Open House (False = an operator saved a newer value first: leave it alone).
                if _roll_open_house(session, settings, value, nxt, now):
                    cleared.append(key)
                continue
        # Conditional on the value we judged: if an operator saved a new date
        # since we read it, the WHERE no longer matches and we leave it alone.
        done = session.execute(
            text("""
                UPDATE app_config
                SET value = '', updated_at = :now, updated_by = :by
                WHERE key = :k AND value = :v
                RETURNING key
            """),
            {"now": now, "by": UPDATED_BY, "k": key, "v": value},
        ).fetchone()
        if done is None:
            continue
        session.execute(
            text("""
                INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'app_config', :k, 'config_expired', :by, CAST(:ctx AS jsonb), :now)
            """),
            {"id": str(uuid.uuid4()), "k": key, "by": UPDATED_BY, "now": now,
             "ctx": '{"expired_value": %s}' % _json(value)},
        )
        cleared.append(key)
        logger.info("date_expiry: cleared %s (was %r)", key, value)
        _notify(settings, key, label, value, now)
    if cleared:
        session.flush()
    return cleared


OPEN_HOUSE_KEY = "next_open_house_date"
OPEN_HOUSE_LINK_KEY = "live_open_house_link"


def next_open_house(today):
    """The earliest Open House on the standing schedule on or after `today`, or None when the schedule has run out."""
    return next((d for d in sorted(OPEN_HOUSES) if d >= today), None)


def _long_date(d) -> str:
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _roll_open_house(session: Session, settings: Any, old_value: str, nxt, now: datetime) -> bool:
    """Replace the passed date with the next one and clear the old event's RSVP link. False if an operator changed it first."""
    new_value = _long_date(nxt)
    done = session.execute(
        text("UPDATE app_config SET value = :new, updated_at = :now, updated_by = :by WHERE key = :k AND value = :old RETURNING key"),
        {"new": new_value, "now": now, "by": UPDATED_BY, "k": OPEN_HOUSE_KEY, "old": old_value},
    ).fetchone()
    if done is None:
        return False
    old_link = session.execute(text("SELECT value FROM app_config WHERE key = :k"), {"k": OPEN_HOUSE_LINK_KEY}).scalar() or ""
    session.execute(
        text("UPDATE app_config SET value = '', updated_at = :now, updated_by = :by WHERE key = :k"),
        {"now": now, "by": UPDATED_BY, "k": OPEN_HOUSE_LINK_KEY},
    )
    session.execute(
        text("""
            INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES (:id, 'app_config', :k, 'config_rolled', :by, CAST(:ctx AS jsonb), :now)
        """),
        {"id": str(uuid.uuid4()), "k": OPEN_HOUSE_KEY, "by": UPDATED_BY, "now": now,
         "ctx": '{"from": %s, "to": %s, "link_cleared": %s}' % (_json(old_value), _json(new_value), _json(bool(old_link)))},
    )
    logger.info("date_expiry: rolled %s %r -> %r (RSVP link cleared)", OPEN_HOUSE_KEY, old_value, new_value)
    _notify_roll(settings, old_value, new_value, now)
    return True


def roll_recipients(settings: Any) -> list[str]:
    """Kes (alert_email_to), Ali and Jackie, no duplicates."""
    from app.core import open_house_reminder as rule

    out: list[str] = []
    for a in [*(getattr(settings, "alert_email_to", None) or "").split(","), rule.ALI, rule.JACKIE]:
        a = a.strip()
        if a and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def _notify_roll(settings: Any, old_value: str, new_value: str, now: datetime) -> None:
    from app.services.alerting import _send_alert_email

    message = (
        f"The Open House date moved from {old_value} to {new_value} automatically (standing schedule: 1st and 3rd Thursday, "
        f"{OPEN_HOUSE_TIME_TEXT}). ACTION NEEDED: the Eventbrite page is one page per event, so the old RSVP link was cleared. Please "
        f"create or find the Eventbrite page for {new_value} and paste its link into dashboard Settings (Open House RSVP link). Until "
        "then, texts and emails give the date and say the RSVP link will be sent. If this Open House is cancelled or moved, tell Kes so "
        "it can be taken off the schedule."
    )
    try:
        _send_alert_email(
            settings=settings, alert_id=str(uuid.uuid4()), alert_type="open_house_date_rolled",
            severity="warning", message=message, now=now, to_override=roll_recipients(settings),
        )
    except Exception as exc:
        logger.error("date_expiry: roll notification email failed: %s", exc)


def _json(value: str) -> str:
    import json

    return json.dumps(value)


def _notify(settings: Any, key: str, label: str, value: str, now: datetime) -> None:
    """One email when a date is cleared, so a blank setting is never a surprise."""
    from app.services.alerting import _send_alert_email

    message = (
        f"The {label} ({value}) has passed and was cleared automatically. Messages now invite "
        "leads to start learning for $0 (no card needed) at https://training.colaberry.com/start-free instead of mentioning a date. "
        "Set the next date in dashboard Settings when one is scheduled."
    )
    try:
        _send_alert_email(
            settings=settings, alert_id=str(uuid.uuid4()), alert_type="date_setting_expired",
            severity="warning", message=message, now=now,
        )
    except Exception as exc:
        logger.error("date_expiry: notification email failed for %s: %s", key, exc)
