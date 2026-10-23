"""Opt-out guard: pause Cora's scheduled texts before GoHighLevel locks the whole account (Kes 2026-10-16).

HighLevel locks a sub-account's SMS for 24 hours when the opt-out rate reaches 3% (delivery errors: 10%). On 2026-10-15 it did
(11 opt-outs on ~230 texts) and every text failed for ~24 h with no warning from our side. This guard watches the same ratio
and stops Cora's follow-up texts first:

  rate  = opt-outs by SMS reply (optout_actions: source sms_reply, kind dnd, applied) in the last 24 h
          / texts GHL reports delivered in the last 24 h (channel_events kind delivery, channel sms)
  pause : rate >= `sms_guard_pause_pct` (default 2.0) with at least `sms_guard_min_sample` texts (default 50)
  resume: rate < `sms_guard_resume_pct` (default 1.5), or too few texts left in the window to judge AND at most
          `sms_guard_resume_max_optouts` (default 1) opt-outs left in it (a small sample with several opt-outs, e.g. when a GHL
          lockout made texts fail, is not evidence that it is safe: 2 opt-outs on 44 texts resumed the guard too early on 2026-10-16)

While paused, `sms_ledger.reserve` BLOCKS source "followup" (Cora's post-call texts). The lead is not texted for that touch and
still gets the email / voice cadence. Texts sent by GHL workflows or staff are outside Cora's control and are not stopped.
No alert or email is raised (Kes 2026-10-16: not wanted); a pause or resume is an app_config change (audit_log, updated_by "auto: sms opt-out guard") and a log line. `sms_guard_enabled` = false switches the guard off.
The state lives in `app_config.sms_guard_state` (ok | paused); it is evaluated each alert cycle and read at send time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

STATE_KEY = "sms_guard_state"

WINDOW_HOURS = 24
DEFAULT_PAUSE_PCT = 2.0
DEFAULT_RESUME_PCT = 1.5
DEFAULT_MIN_SAMPLE = 50
DEFAULT_RESUME_MAX_OPTOUTS = 1


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
    resume_max = int(_float(session, settings, "sms_guard_resume_max_optouts", DEFAULT_RESUME_MAX_OPTOUTS))
    paused = _cfg(session, settings, STATE_KEY, "ok").strip().lower() == "paused"
    stats = f"{optouts} opt-outs by text reply on {sent} texts delivered in the last {WINDOW_HOURS} h = {pct:.1f}%"

    if not paused and sent >= min_sample and pct >= pause_pct:
        set_config_value(session, STATE_KEY, "paused", updated_by="auto: sms opt-out guard")
        logger.info("sms_guard: PAUSED Cora follow-up texts | %s (guard line %g%%)", stats, pause_pct)
        return "paused_now"
    if paused and (pct < resume_pct or (sent < min_sample and optouts <= resume_max)):
        set_config_value(session, STATE_KEY, "ok", updated_by="auto: sms opt-out guard")
        logger.info("sms_guard: RESUMED Cora follow-up texts | %s (resume line %g%%)", stats, resume_pct)
        return "resumed"
    return "paused" if paused else "ok"


def status(session: Session, settings: Any, now: datetime | None = None) -> dict[str, Any]:
    """Read-only snapshot for the dashboard (Delivery Health): is Cora's follow-up text paused, and why. Never raises.

    `reason` is plain English; `resumes_when` states the exact resume rule; `next_optout_leaves_at` is when the oldest opt-out
    counted today leaves the 24 h window (the earliest moment the rate can fall without new texts)."""
    now = now or datetime.now(tz=timezone.utc)
    try:
        on = enabled(session, settings)
        pause_pct = _float(session, settings, "sms_guard_pause_pct", DEFAULT_PAUSE_PCT)
        resume_pct = _float(session, settings, "sms_guard_resume_pct", DEFAULT_RESUME_PCT)
        min_sample = int(_float(session, settings, "sms_guard_min_sample", DEFAULT_MIN_SAMPLE))
        resume_max = int(_float(session, settings, "sms_guard_resume_max_optouts", DEFAULT_RESUME_MAX_OPTOUTS))
        optouts, sent, pct = rate(session, now)
        paused = on and _cfg(session, settings, STATE_KEY, "ok").strip().lower() == "paused"
        since = session.execute(text("SELECT MAX(updated_at) FROM app_config WHERE key = :k"), {"k": STATE_KEY}).scalar()
        oldest = session.execute(text(
            "SELECT MIN(created_at) FROM optout_actions WHERE source = 'sms_reply' AND kind = 'dnd' AND status = 'applied' "
            "AND created_at >= :s"), {"s": now - timedelta(hours=WINDOW_HOURS)}).scalar()
        if isinstance(oldest, str):
            oldest = datetime.fromisoformat(oldest)
        if oldest is not None and oldest.tzinfo is None:
            oldest = oldest.replace(tzinfo=timezone.utc)
        if isinstance(since, str):
            since = datetime.fromisoformat(since)
        reason = None
        if paused:
            reason = (f"Cora's follow-up texts are paused: {optouts} opt-out(s) by text reply on {sent} texts delivered in the last "
                      f"{WINDOW_HOURS} h = {pct:.1f}% (pause line {pause_pct:g}%).")
        return {
            "enabled": on, "paused": bool(paused), "optouts": optouts, "delivered": sent, "pct": round(pct, 1),
            "pause_pct": pause_pct, "resume_pct": resume_pct, "min_sample": min_sample, "resume_max_optouts": resume_max,
            "reason": reason,
            "resumes_when": (f"Resumes automatically when the opt-out rate drops below {resume_pct:g}%, or when fewer than "
                             f"{min_sample} texts were delivered and at most {resume_max} opt-out(s) remain in the {WINDOW_HOURS} h window."),
            "paused_since": since.isoformat() if paused and since else None,
            "next_optout_leaves_at": (oldest + timedelta(hours=WINDOW_HOURS)).isoformat() if oldest else None,
        }
    except Exception as exc:
        logger.warning("sms_guard: status read failed: %s", exc)
        return {"enabled": None, "paused": False, "error": str(exc)[:200]}

