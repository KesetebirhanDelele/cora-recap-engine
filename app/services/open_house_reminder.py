"""
Open House reminder + missing-date alert (Kes, 2026-11-10). Pure rules live in app/core/open_house_reminder.py.

Runs every metrics cycle (60 s) and is idempotent: each email is claimed first with an app_config row (primary key = one send per
Open House / per Central day), so concurrent workers and restarts never send twice.

Send failures are never silent: a failed send releases its claim and is retried on the next cycle (up to MAX_ATTEMPTS); if every
recipient fails for that long, or only some recipients got the mail, Kes gets a dashboard exception plus an email naming who missed it.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import open_house_reminder as rule
from app.core.orientation import OPEN_HOUSES, TIMEZONE

logger = logging.getLogger(__name__)

SETTING_KEY = "next_open_house_date"
MAX_ATTEMPTS = 5          # one try per 60-second cycle; then stop retrying and tell Kes


def recipients(settings: Any) -> list[str]:
    """Kes (alert_email_to) plus Jackie, no duplicates."""
    out: list[str] = []
    for a in [*(getattr(settings, "alert_email_to", None) or "").split(","), rule.JACKIE]:
        a = a.strip()
        if a and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    return out


def _claim(session: Session, key: str, now: datetime) -> bool:
    row = session.execute(text(
        "INSERT INTO app_config (key, value, description, \"group\", updated_at, updated_by) "
        "VALUES (:k, :v, 'Open House reminder ledger (one send per key)', 'ledger', :now, 'open_house_reminder') "
        "ON CONFLICT (key) DO NOTHING RETURNING key"), {"k": key, "v": now.isoformat(), "now": now}).fetchone()
    session.commit()
    return row is not None


def _release(session: Session, key: str) -> None:
    session.execute(text("DELETE FROM app_config WHERE key = :k"), {"k": key})
    session.commit()


def _bump_failures(session: Session, key: str, now: datetime) -> int:
    """Count one more failed attempt for this send and return the total (persisted, so a restart does not reset it)."""
    n = session.execute(text(
        "INSERT INTO app_config (key, value, description, \"group\", updated_at, updated_by) "
        "VALUES (:k, '1', 'Open House reminder failed attempts', 'ledger', :now, 'open_house_reminder') "
        "ON CONFLICT (key) DO UPDATE SET value = (app_config.value::int + 1)::text, updated_at = :now RETURNING value"),
        {"k": f"open_house_fail:{key}", "now": now}).scalar()
    session.commit()
    return int(n)


def _deliver(send: Callable[[list[str]], None], to: list[str]) -> tuple[list[str], list[str], str]:
    """Send to everyone at once; if that fails, one by one so a single bad address cannot block the others.
    Returns (delivered, failed, last_error)."""
    try:
        send(to)
        return list(to), [], ""
    except Exception as first:
        ok: list[str] = []
        bad: list[str] = []
        err = str(first)
        for addr in to:
            try:
                send([addr])
                ok.append(addr)
            except Exception as exc:
                bad.append(addr)
                err = str(exc)
        return ok, bad, err


def _tell_kes(session: Session, settings: Any, what: str, failed: list[str], error: str) -> None:
    """Dashboard exception (always) plus an email to Kes (it may fail too; the dashboard record still stands)."""
    from app.services.alerting import _smtp_send
    from app.worker.exceptions import create_exception

    msg = f"{what} did not reach: {', '.join(failed)}. Error: {error[:200]}"
    try:
        create_exception(session, type="open_house_email_failed", severity="warning",
                         context={"message": msg, "failed": failed, "what": what}, entity_type="open_house", entity_id=what[:60])
        session.commit()
    except Exception as exc:
        logger.error("open_house_reminder: could not record the failure: %s", exc)
    kes = [a.strip() for a in (getattr(settings, "alert_email_to", None) or "").split(",") if a.strip()]
    try:
        _smtp_send(settings, [a for a in kes if a not in failed] or kes, "Cora could not send an Open House email",
                   msg + "\n\nThe Open House reminder or alert did not go out to the people listed. Please tell them by hand.",
                   log_label="open_house_email_failed", raise_on_failure=True)
    except Exception as exc:
        logger.error("open_house_reminder: failure notice email also failed: %s", exc)


def _send_once(session: Session, settings: Any, key: str, what: str, to: list[str],
               send: Callable[[list[str]], None], now: datetime) -> bool:
    """Claim, deliver, and handle failure. True if at least one recipient got it."""
    if not _claim(session, key, now):
        return False
    ok, bad, err = _deliver(send, to)
    if ok:
        if bad:                                         # partly delivered: keep the claim (no duplicates), tell Kes who missed out
            _tell_kes(session, settings, what, bad, err)
        return True
    attempts = _bump_failures(session, key, now)
    logger.error("open_house_reminder: %s failed (attempt %d/%d): %s", what, attempts, MAX_ATTEMPTS, err)
    if attempts >= MAX_ATTEMPTS:                        # give up visibly: keep the claim so it does not loop forever
        _tell_kes(session, settings, what, bad, err)
    else:
        _release(session, key)                          # retry next cycle
    return False


def run_open_house_reminders(session: Session, settings: Any, now: datetime) -> dict[str, Any]:
    """Send the day-after reminder and, if there is no clear Open House date, the daily alert. Returns what was done."""
    from app.core.app_config import get_str
    from app.services.alerting import _send_alert_email, _smtp_send

    local = now.astimezone(ZoneInfo(TIMEZONE))
    today, hour = local.date(), local.hour
    done: dict[str, Any] = {"reminder": None, "missing_alert": None}
    to = recipients(settings)

    passed = rule.reminder_due(today, hour)
    if passed is not None:
        nxt = rule.next_after(passed)
        remaining = sorted(d for d in OPEN_HOUSES if d >= today)
        subject, body = rule.build_reminder(passed, nxt, remaining)
        if _send_once(session, settings, f"open_house_reminder:{passed.isoformat()}", f"Open House reminder for {passed.isoformat()}", to,
                      lambda addrs: _smtp_send(settings, addrs, subject, body, log_label="open_house_reminder", raise_on_failure=True), now):
            done["reminder"] = passed.isoformat()

    if hour >= rule.SEND_FROM_HOUR:
        reason = rule.missing_reason(get_str(SETTING_KEY, session, settings, ""), today)
        # On the day the reminder goes out the date is blank by design (the setting clears the day after the Open House), so the
        # reminder already covers it: the alert starts the following day.
        covered_by_reminder = passed is not None and passed + timedelta(days=1) == today
        if reason and not covered_by_reminder:
            msg = rule.build_missing_alert(reason)
            if _send_once(session, settings, f"open_house_missing:{today.isoformat()}", f"Open House missing-date alert for {today.isoformat()}", to,
                          lambda addrs: _send_alert_email(settings=settings, alert_id=str(uuid.uuid4()), alert_type="open_house_date_missing",
                                                          severity="warning", message=msg, now=now, to_override=addrs,
                                                          raise_on_failure=True), now):
                done["missing_alert"] = reason
    return done
