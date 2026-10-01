"""
SMS ledger + pre-send gate I/O (spec/34).

One row in `sms_send_ledger` per text Cora tries to send. `reserve()` is called right BEFORE the
text is written to GHL (the write is what sends it): it runs app/core/sms_gate.py against the
Pacific-day budget, records the verdict, and - when allowed - holds the segments so two workers can
never both spend the last of the budget (a transaction-scoped Postgres advisory lock serialises the
check-and-insert). The caller then calls mark_sent() or mark_failed().

Status: reserved (allowed, GHL write pending) | sent | failed (write failed - not counted) |
        blocked (content rule - never sent) | deferred (budget used - moves to next day).
Budget = segments of rows in (reserved, sent) since 00:00 US Pacific.
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import sms_gate as gate

logger = logging.getLogger(__name__)

SOURCES = ("followup", "correction", "test")
_LOCK_KEY = 7_340_034            # arbitrary, app-wide advisory lock id for the SMS budget
_COUNTED = "('reserved','sent')"


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    decision: gate.GateDecision
    ledger_id: str | None = None
    defer_until: datetime | None = None    # when to try again (UTC); None for BLOCK / ALLOW

    @property
    def reason(self) -> str:
        return self.decision.reason

    @property
    def code(self) -> str:
        return self.decision.code


def _config(session: Session, settings: Any) -> gate.GateConfig:
    from app.core.app_config import get_str
    from app.services.wrong_date_monitor import expected_dates

    class_start, open_house = expected_dates(session, settings)
    return gate.config_from(get_str, session, settings, class_start, open_house)


def _aware(v: Any) -> datetime:
    """Timestamps as UTC-aware datetimes (sqlite, used in tests, hands back strings / naive values)."""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def usage(session: Session, now: datetime) -> gate.GateUsage:
    start = gate.pacific_day_start_utc(now)
    segs = session.execute(
        text(f"SELECT COALESCE(SUM(segments),0) FROM sms_send_ledger "
             f"WHERE status IN {_COUNTED} AND created_at >= :s"), {"s": start}).scalar() or 0
    last = session.execute(
        text(f"SELECT MAX(created_at) FROM sms_send_ledger WHERE status IN {_COUNTED}")).scalar()
    since = None
    if last is not None:
        since = max((now - _aware(last)).total_seconds(), 0.0)
    minute = session.execute(
        text(f"SELECT COUNT(*) FROM sms_send_ledger WHERE status IN {_COUNTED} AND created_at >= :m"),
        {"m": now - timedelta(seconds=60)}).scalar() or 0
    return gate.GateUsage(int(segs), since, int(minute))


def _insert(session: Session, *, status: str, contact_id: str, source: str, body: str,
            segments: int, code: str, reason: str, now: datetime) -> str:
    rid = str(uuid.uuid4())
    session.execute(
        text("""
            INSERT INTO sms_send_ledger
                (id, contact_id, source, status, code, reason, segments, body, pacific_day, created_at)
            VALUES (:id, :c, :src, :st, :code, :reason, :seg, :body, :day, :now)
        """),
        {"id": rid, "c": contact_id or "unknown", "src": source, "st": status, "code": code,
         "reason": (reason or "")[:500], "seg": segments, "body": body or "",
         "day": now.astimezone(gate.PACIFIC).date(), "now": now})
    return rid


def reserve(
    settings: Any, *, contact_id: str, body: str, source: str, tz_name: str = "America/Chicago",
    now: datetime | None = None, session_factory: Callable[[], Any] | None = None,
) -> Reservation:
    """Gate one outgoing SMS. Own short transaction, committed before returning."""
    if source not in SOURCES:
        raise ValueError(f"unknown SMS source {source!r}")
    if session_factory is None:
        from app.db import get_sync_session as session_factory  # type: ignore[assignment]
    now = now or datetime.now(tz=timezone.utc)

    with session_factory() as s:
        if s.get_bind().dialect.name == "postgresql":
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _LOCK_KEY})
        cfg = _config(s, settings)
        before = usage(s, now)
        d = gate.decide(body, before, cfg)

        if d.action == gate.ALLOW:
            rid = _insert(s, status="reserved", contact_id=contact_id, source=source, body=body,
                          segments=d.segments, code="ok", reason="", now=now)
            res = Reservation(True, d, rid)
            after = before.segments_today + d.segments
        elif d.action == gate.BLOCK:
            _insert(s, status="blocked", contact_id=contact_id, source=source, body=body,
                    segments=d.segments, code=d.code, reason=d.reason, now=now)
            res, after = Reservation(False, d), before.segments_today
        else:
            until = None
            if d.next_day:
                until = gate.next_legal_send_time(
                    gate.next_pacific_day_start_utc(now) + timedelta(minutes=5), tz_name)
                _insert(s, status="deferred", contact_id=contact_id, source=source, body=body,
                        segments=d.segments, code=d.code, reason=d.reason, now=now)
            else:
                until = now + timedelta(seconds=d.retry_after_seconds or 30)
            res, after = Reservation(False, d, None, until), before.segments_today

        level = gate.usage_level(after, cfg.effective_daily_cap)
        if d.code == "daily_cap":
            level = "exhausted"
        if level != "ok":
            _alert_once_per_day(s, settings, level, after, cfg.effective_daily_cap, now)
        s.commit()
    if res.allowed:
        return res
    logger.warning("sms_gate: %s | code=%s contact=%s source=%s | %s",
                   "BLOCKED" if d.action == gate.BLOCK else "DEFERRED", d.code, contact_id, source, d.reason)
    return res


def factory_for(session: Session) -> Callable[[], Any]:
    """Short-lived sessions on the SAME database as `session` (so the gate and its caller always agree)."""
    @contextmanager
    def _open():
        with Session(session.get_bind()) as s:
            yield s
    return _open


def mark_sent(ledger_id: str, session_factory: Callable[[], Any] | None = None) -> None:
    _set_status(ledger_id, "sent", "", session_factory)


def mark_failed(ledger_id: str, reason: str, session_factory: Callable[[], Any] | None = None) -> None:
    """GHL write failed -> the text was not sent, so its segments are released."""
    _set_status(ledger_id, "failed", reason, session_factory)


def _set_status(ledger_id: str, status: str, reason: str, session_factory: Callable[[], Any] | None) -> None:
    if session_factory is None:
        from app.db import get_sync_session as session_factory  # type: ignore[assignment]
    with session_factory() as s:
        s.execute(text("UPDATE sms_send_ledger SET status = :st, reason = :r WHERE id = :id AND status = 'reserved'"),
                  {"st": status, "r": (reason or "")[:500], "id": ledger_id})
        s.commit()


def _alert_once_per_day(session: Session, settings: Any, level: str, used: int, cap: int,
                        now: datetime) -> None:
    """One audit row + one email per Pacific day per level (warning at 80%, exhausted at the cap)."""
    action = f"sms_budget_{level}"
    day_start = gate.pacific_day_start_utc(now)
    seen = session.execute(
        text("SELECT 1 FROM audit_log WHERE action = :a AND created_at >= :d LIMIT 1"),
        {"a": action, "d": day_start}).fetchone()
    if seen:
        return
    session.execute(
        text("""INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'sms_budget', 'daily', :a, 'system', CAST(:ctx AS jsonb), :now)"""),
        {"id": str(uuid.uuid4()), "a": action, "now": now,
         "ctx": json.dumps({"segments_today": used, "cap": cap})})
    msg = (f"Cora SMS budget {'EXHAUSTED' if level == 'exhausted' else 'at 80%'}: {used}/{cap} segments "
           f"today (Pacific day). Twilio sole-proprietor limit is 1,000/day to T-Mobile; "
           + ("remaining texts move to tomorrow." if level == "exhausted" else "watch the SMS Monitor tile."))
    try:
        from app.services.alerting import _send_alert_email

        _send_alert_email(settings=settings, alert_id=str(uuid.uuid4()), alert_type="sms_daily_budget",
                          severity="warning" if level == "warning" else "critical", message=msg, now=now)
    except Exception as exc:                       # the audit row + tile still show it
        logger.error("sms budget alert email failed: %s", exc)


# ── dashboard snapshot ───────────────────────────────────────────────────────

def snapshot(session: Session, settings: Any, now: datetime | None = None) -> dict[str, Any]:
    """Everything the SMS Monitor tile shows."""
    now = now or datetime.now(tz=timezone.utc)
    cfg = _config(session, settings)
    cap = cfg.effective_daily_cap
    start = gate.pacific_day_start_utc(now)
    u = usage(session, now)

    def rows(sql: str, **p: Any) -> list[Any]:
        return session.execute(text(sql), {"s": start, **p}).fetchall()

    by_status = {r[0]: (int(r[1]), int(r[2] or 0)) for r in rows(
        "SELECT status, COUNT(*), SUM(segments) FROM sms_send_ledger WHERE created_at >= :s GROUP BY status")}
    by_source = {r[0]: int(r[1]) for r in rows(
        f"SELECT source, COUNT(*) FROM sms_send_ledger WHERE created_at >= :s AND status IN {_COUNTED} GROUP BY source")}
    hourly = {}
    for r in rows(f"SELECT created_at, segments FROM sms_send_ledger WHERE created_at >= :s AND status IN {_COUNTED}"):
        h = _aware(r[0]).astimezone(gate.PACIFIC).strftime("%H:00")
        hourly[h] = hourly.get(h, 0) + int(r[1])
    recent = [
        {"at": _aware(r[0]).isoformat(), "source": r[1],
         "status": r[2], "code": r[3], "segments": int(r[4]), "contact_id": r[5], "reason": r[6] or "",
         "preview": (r[7] or "")[:90]}
        for r in rows("""SELECT created_at, source, status, code, segments, contact_id, reason, body
                         FROM sms_send_ledger ORDER BY created_at DESC LIMIT 25""")]
    return {
        "day_resets_at": gate.next_pacific_day_start_utc(now).isoformat(),
        "daily_cap": cap, "hard_max": gate.HARD_MAX_DAILY_SEGMENTS,
        "segments_today": u.segments_today, "remaining": max(cap - u.segments_today, 0),
        "level": gate.usage_level(u.segments_today, cap),
        "messages_today": sum(v[0] for k, v in by_status.items() if k in ("reserved", "sent")),
        "blocked_today": by_status.get("blocked", (0, 0))[0],
        "deferred_today": by_status.get("deferred", (0, 0))[0],
        "failed_today": by_status.get("failed", (0, 0))[0],
        "by_source": by_source, "by_hour_pacific": dict(sorted(hourly.items())),
        "limits": {"min_gap_seconds": cfg.min_gap_seconds, "per_minute_cap": cfg.per_minute_cap,
                   "max_segments_per_message": cfg.max_segments_per_message},
        "recent": recent,
    }
