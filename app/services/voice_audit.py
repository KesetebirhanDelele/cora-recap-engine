"""
Voice-call audit (spec/35 "voice audit", Kes 2026-10-20): reads GHL Voice AI call logs (the AI Receptionist and any other Voice
AI agent) and flags what the BOT said that names a retired course, makes an employment claim or uses the word "free".

Texts and emails are audited in delivery_sync; calls never were. The flag is written as channel_events kind 'content_flag',
channel 'voice', source 'ghl_voice_ai', so the existing hourly alert (channel_health.run_content_flag_check) reports it and
labels its origin. A call is scanned once (kind 'voice_seen' marks it), so replays never alert twice.

Failure is never silent: if the call logs cannot be read, ONE `ghl_voice_audit_error` alert says the audit is not running and
why, and it resolves itself on the next successful read.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.core import content_audit as ca
from app.core import offer
from app.core import voice_audit as va

logger = logging.getLogger(__name__)

VOICE_CHANNEL = "voice"
SOURCE = "ghl_voice_ai"
ERROR_ALERT = "ghl_voice_audit_error"
RETRY_NO_TRANSCRIPT_AFTER = timedelta(hours=2)      # a transcript can land a little after the call; give up on it after this


def _parse_ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _upsert(session: Session, *, kind: str, log: dict, at: datetime, now: datetime, error: str | None = None,
            detail: dict | None = None) -> None:
    session.execute(
        text("""INSERT INTO channel_events
                  (id, kind, channel, contact_id, external_id, status_raw, outcome, error, source, event_at, checked_at, detail)
                VALUES (:id, :k, 'voice', :c, :x, NULL, NULL, :e, :src, :at, :now, CAST(:d AS jsonb))
                ON CONFLICT (kind, channel, external_id) WHERE external_id IS NOT NULL DO NOTHING"""),
        {"id": str(uuid.uuid4()), "k": kind, "c": log.get("contactId") or "", "x": log["id"], "e": error, "src": SOURCE,
         "at": at, "now": now, "d": json.dumps(detail or {})})


def _set_failure(session: Session, settings: Any, now: datetime, err: str) -> None:
    from app.models.alert_event import AlertEvent
    from app.services.alerting import _send_alert_email

    active = session.execute(text("SELECT id FROM alert_events WHERE alert_type = :t AND status = 'active' LIMIT 1"),
                             {"t": ERROR_ALERT}).fetchone()
    if active:
        session.execute(text("UPDATE alert_events SET last_seen_at = :n WHERE id = :i"), {"n": now, "i": active[0]})
        return
    msg = (f"AI Receptionist call audit could NOT read GHL Voice AI call logs: {err[:300]}. Calls are not being checked for "
           f"retired courses, employment claims or the word 'free' until this clears. Origin: Cora's voice-audit job "
           f"(app/services/voice_audit.py) calling GET /voice-ai/dashboard/call-logs with the Private Integration token - "
           f"check that token's Voice AI read scope and GHL status; this is not a problem in the AI Receptionist itself.")
    aid = str(uuid.uuid4())
    session.add(AlertEvent(id=aid, alert_type=ERROR_ALERT, severity="warning", status="active", message=msg,
                           email_sent_at=now, last_seen_at=now, created_at=now))
    _send_alert_email(settings=settings, alert_id=aid, alert_type=ERROR_ALERT, severity="warning", message=msg, now=now)


def _clear_failure(session: Session, now: datetime) -> None:
    session.execute(text("UPDATE alert_events SET status = 'resolved', resolved_at = :n "
                         "WHERE alert_type = :t AND status = 'active'"), {"n": now, "t": ERROR_ALERT})


def audit_voice_calls(session: Session, settings: Any, now: datetime | None = None, *, ghl: Any = None, judge: Any = None,
                      lookback_hours: int = 48, page_size: int = 25, max_pages: int = 8) -> dict:
    """Scan recent Voice AI calls not yet scanned. Returns counts. Never raises (a failed read becomes an alert)."""
    from app.core.app_config import get_config_value

    now = now or datetime.now(tz=timezone.utc)
    stats = {"scanned": 0, "flagged": 0, "cleared": 0, "waiting": 0, "skipped": False, "error": None}

    def cfg(key: str, default: str | None) -> str | None:
        return get_config_value(key, session, settings, default) if settings is not None else default

    if str(cfg("voice_audit_enabled", "true")).lower() not in ("true", "1", "yes"):
        stats["skipped"] = True
        return stats
    try:
        if ghl is None:
            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
        terms = offer.parse_terms(cfg("offer_forbidden_terms", None))
        brand = ca.parse_brand_phrases(cfg("brand_phrases", None))
        llm_on = str(cfg("content_llm_enabled", "true")).lower() in ("true", "1", "yes")
        if judge is None:
            from app.services import content_judge

            judge = content_judge.judge
        cutoff = now - timedelta(hours=lookback_hours)

        for page in range(1, max_pages + 1):
            logs = ghl.list_voice_call_logs(page=page, page_size=page_size)
            in_window = [lg for lg in logs if lg.get("id") and (_parse_ts(lg.get("createdAt")) or now) >= cutoff]
            seen: set[str] = set()
            if in_window:
                seen = {r[0] for r in session.execute(
                    text("SELECT external_id FROM channel_events WHERE channel = 'voice' AND external_id IN :ids "
                         "AND kind IN ('voice_seen', 'content_flag')").bindparams(bindparam("ids", expanding=True)),
                    {"ids": [lg["id"] for lg in in_window]}).fetchall()}
            fresh = [lg for lg in in_window if lg["id"] not in seen]
            for lg in fresh:
                _scan_one(session, settings, lg, terms, brand, judge if llm_on else None, now, stats)
            # newest-first: keep paging only while this whole page was new and inside the window
            if len(logs) < page_size or len(in_window) < len(logs) or len(fresh) < len(in_window):
                break
        _clear_failure(session, now)
    except Exception as exc:
        logger.error("voice_audit: could not read/scan GHL Voice AI call logs: %s", exc)
        stats["error"] = str(exc)
        try:
            session.rollback()
            _set_failure(session, settings, now, str(exc))
        except Exception as exc2:
            logger.error("voice_audit: could not raise the failure alert: %s", exc2)
    return stats


def _scan_one(session: Session, settings: Any, lg: dict, terms: tuple[str, ...], brand: tuple[str, ...], judge: Any,
              now: datetime, stats: dict) -> None:
    at = _parse_ts(lg.get("createdAt")) or now
    transcript = lg.get("transcript")
    if not (transcript or "").strip():
        if now - at < RETRY_NO_TRANSCRIPT_AFTER:
            stats["waiting"] += 1
            return                                      # transcript may still be on its way; look again next cycle
        _upsert(session, kind="voice_seen", log=lg, at=at, now=now, detail={"no_transcript": True})
        return
    base = {"origin": va.origin_label(lg.get("agentId")), "agent_id": lg.get("agentId"), "call_id": lg["id"],
            "from_number": lg.get("fromNumber"), "duration": lg.get("duration")}
    finding = va.scan_call(transcript, terms, brand)
    stats["scanned"] += 1
    if finding is None:
        _upsert(session, kind="voice_seen", log=lg, at=at, now=now, detail=base)
        return
    verdict = None
    if finding.course_only and judge is not None:
        try:
            said = " ".join(va.bot_turns(transcript))
            verdict = judge(settings, lg["id"], said, finding.prior_human, terms=list(finding.terms))
        except Exception as exc:                        # fail closed: any doubt leaves the call flagged
            logger.warning("voice_audit: judge failed for %s: %s", lg["id"], exc)
    if verdict == "not_offering":
        _upsert(session, kind="content_ok", log=lg, at=at, now=now, detail={**base, "terms": finding.terms, "verdict": verdict})
        _upsert(session, kind="voice_seen", log=lg, at=at, now=now, detail=base)
        stats["cleared"] += 1
        return
    detail = {**base, "terms": finding.terms, "snippet": finding.excerpt}
    if verdict:
        detail["llm_verdict"] = verdict
    _upsert(session, kind="content_flag", log=lg, at=at, now=now, error=", ".join(finding.terms)[:290], detail=detail)
    stats["flagged"] += 1
