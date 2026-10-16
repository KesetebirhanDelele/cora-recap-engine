"""Opt-out guard: pause Cora's scheduled texts before GoHighLevel locks the whole account (Kes 2026-10-16).

HighLevel locks a sub-account's SMS for 24 hours when the opt-out rate reaches 3% (delivery errors: 10%). On 2026-10-15 it did
(11 opt-outs on ~230 texts) and every text failed for ~24 h with no warning from our side. This guard watches the same ratio
and stops Cora's follow-up texts first:

  rate  = opt-outs by SMS reply (optout_actions: source sms_reply, kind dnd, applied) in the last 24 h
          / texts GHL reports delivered in the last 24 h (channel_events kind delivery, channel sms)
  pause : rate >= `sms_guard_pause_pct` (default 2.0) with at least `sms_guard_min_sample` texts (default 50)
  resume: rate < `sms_guard_resume_pct` (default 1.5), or too few texts left in the window to judge

While paused, `sms_ledger.reserve` BLOCKS source "followup" (Cora's post-call texts). The lead is not texted for that touch and
still gets the email / voice cadence. Texts sent by GHL workflows or staff are outside Cora's control and are not stopped.
Every pause and resume writes an alert (dashboard + email). `sms_guard_enabled` = false switches the guard off.
The state lives in `app_config.sms_guard_state` (ok | paused); it is evaluated each alert cycle and read at send time.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STATE_KEY = "sms_guard_state"
ALERT_TYPE = "sms_optout_guard"
WINDOW_HOURS = 24
DEFAULT_PAUSE_PCT = 2.0
DEFAULT_RESUME_PCT = 1.5
DEFAULT_MIN_SAMPLE = 50


def _cfg(session: Session, settings: Any, key: str, default: str) -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


def _float(session: Session, settings: Any, key: str, default: float) -> float:
    try:
        return float(_cfg(session, settings, key, str(default)))
    except (TypeError, ValueError):
        return default


def enabled(session: Session, settings: Any) -> bool:
    return _cfg(session, settings, "sms_guard_enabled", "true").strip().lower() in ("true", "1", "yes")


def rate(session: Session, now: datetime | None = None) -> tuple[int, int, float]:
    """(opt-outs, texts delivered, percent) over the last 24 h."""
    now = now or datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=WINDOW_HOURS)
    optouts = session.execute(text(
        "SELECT count(*) FROM optout_actions WHERE source = 'sms_reply' AND kind = 'dnd' AND status = 'applied' "
        "AND created_at >= :s AND created_at <= :n"), {"s": since, "n": now}).scalar() or 0
    sent = session.execute(text(
        "SELECT count(*) FROM channel_events WHERE kind = 'delivery' AND channel = 'sms' AND outcome = 'delivered' "
        "AND event_at >= :s AND event_at <= :n"), {"s": since, "n": now}).scalar() or 0
    return int(optouts), int(sent), (100.0 * optouts / sent if sent else 0.0)


def is_paused(session: Session, settings: Any) -> str | None:
    """The reason Cora's follow-up texts are paused, or None. Never raises (a broken guard must not stop texts by itself)."""
    try:
        if not enabled(session, settings):
            return None
        if _cfg(session, settings, STATE_KEY, "ok").strip().lower() != "paused":
            return None
        return "opt-out guard: the 24 h opt-out rate is too high, scheduled texts are paused"
    except Exception as exc:
        logger.warning("sms_guard: state read failed: %s", exc)
        return None


def _alert(session: Session, settings: Any, title: str, detail: str, now: datetime, severity: str) -> None:
    try:
        from app.models.alert_event import AlertEvent
        from app.services.alerting import _send_alert_email

        msg = f"{title}\n{detail}"
        aid = str(uuid.uuid4())
        session.add(AlertEvent(id=aid, alert_type=ALERT_TYPE, severity=severity, status="active", message=msg,
                               email_sent_at=now, last_seen_at=now, created_at=now))
        session.flush()
        _send_alert_email(settings=settings, alert_id=aid, alert_type=ALERT_TYPE, severity=severity, message=msg, now=now)
    except Exception as exc:
        logger.warning("sms_guard: alert failed: %s", exc)


def evaluate(session: Session, settings: Any, now: datetime | None = None) -> str:
    """One check (run each alert cycle). Returns 'off', 'ok', 'paused', 'paused_now' or 'resumed'. Caller commits."""
    from app.core.app_config import set_config_value

    now = now or datetime.now(tz=timezone.utc)
    if not enabled(session, settings):
        return "off"
    pause_pct = _float(session, settings, "sms_guard_pause_pct", DEFAULT_PAUSE_PCT)
    resume_pct = _float(session, settings, "sms_guard_resume_pct", DEFAULT_RESUME_PCT)
    min_sample = int(_float(session, settings, "sms_guard_min_sample", DEFAULT_MIN_SAMPLE))
    optouts, sent, pct = rate(session, now)
    paused = _cfg(session, settings, STATE_KEY, "ok").strip().lower() == "paused"
    stats = f"{optouts} opt-outs by text reply on {sent} texts delivered in the last {WINDOW_HOURS} h = {pct:.1f}%"

    if not paused and sent >= min_sample and pct >= pause_pct:
        set_config_value(session, STATE_KEY, "paused", updated_by="auto: sms opt-out guard")
        _alert(session, settings, "Scheduled texts PAUSED: opt-out rate reached the guard line.",
               f"{stats} (guard line {pause_pct:g}%; GoHighLevel locks all texts for 24 h at 3%). Cora's follow-up texts "
               f"are paused; leads still get email and voice. Texts sent by GHL workflows or staff are not stopped by this "
               f"guard. It resumes by itself below {resume_pct:g}%.", now, "warning")
        return "paused_now"
    if paused and (sent < min_sample or pct < resume_pct):
        set_config_value(session, STATE_KEY, "ok", updated_by="auto: sms opt-out guard")
        session.execute(text("UPDATE alert_events SET status = 'resolved', resolved_at = :n WHERE alert_type = :t AND status = 'active'"),
                        {"n": now, "t": ALERT_TYPE})
        _alert(session, settings, "Scheduled texts RESUMED.", f"{stats} (resume line {resume_pct:g}%).", now, "warning")
        session.execute(text("UPDATE alert_events SET status = 'resolved', resolved_at = :n WHERE alert_type = :t AND status = 'active'"),
                        {"n": now, "t": ALERT_TYPE})
        return "resumed"
    return "paused" if paused else "ok"
