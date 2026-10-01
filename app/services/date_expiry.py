"""
Date expiry (spec/32).

Once the next-class-start / next-open-house date has PASSED, clear the dashboard
setting (set it to "") so no past date is ever told to a lead again. Prompts
then switch to inviting the lead to start free at myfreeaiclass.com
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


def _json(value: str) -> str:
    import json

    return json.dumps(value)


def _notify(settings: Any, key: str, label: str, value: str, now: datetime) -> None:
    """One email when a date is cleared, so a blank setting is never a surprise."""
    from app.services.alerting import _send_alert_email

    message = (
        f"The {label} ({value}) has passed and was cleared automatically. Messages now invite "
        "leads to start learning for free at www.myfreeaiclass.com instead of mentioning a date. "
        "Set the next date in dashboard Settings when one is scheduled."
    )
    try:
        _send_alert_email(
            settings=settings, alert_id=str(uuid.uuid4()), alert_type="date_setting_expired",
            severity="warning", message=message, now=now,
        )
    except Exception as exc:
        logger.error("date_expiry: notification email failed for %s: %s", key, exc)
