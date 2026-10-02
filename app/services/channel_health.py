"""
Per-channel delivery health: sent vs delivered, last delivery, silence alerts (spec/35).

Hand-offs (what Cora asked GHL to send) are recorded in channel_events by the code that writes the
trigger field; deliveries come from delivery_sync (email / SMS) and call_events (calls). A hand-off is
matched to a delivery for the same contact + channel inside [-2 min, +60 min] of the hand-off.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import channel_health as ch

logger = logging.getLogger(__name__)

ALERT_PREFIX = "channel_silence_"
DEFAULT_CC = "ali@colaberry.com"
CALL_JOB_CONTACT = "COALESCE(j.payload_json->>'contact_id', j.entity_id)"

# outcome expression for call_events (mirrors channel_health.call_outcome)
CALL_OK_SQL = ("(lower(coalesce(c.end_call_reason,'')) IN ('voicemail','human_pick_up_cut_off','human_goodbye','agent_goodbye') "
               "OR (lower(coalesce(c.end_call_reason,'')) IN ('undefined','') AND coalesce(c.duration_seconds,0) >= 5))")


def _cfg(session: Session, settings: Any, key: str, default: str) -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


def thresholds(session: Session, settings: Any) -> ch.Thresholds:
    def f(key: str, default: float) -> float:
        try:
            return float(_cfg(session, settings, key, str(default)))
        except ValueError:
            return default

    return ch.Thresholds(silence_hours=f("channel_silence_hours", 60.0),
                         amber_silence_hours=f("channel_silence_amber_hours", 36.0),
                         confirm_minutes=int(f("delivery_confirm_minutes", 15)))


# ── hand-offs ────────────────────────────────────────────────────────────────

def record_handoff(session: Session, channel: str, contact_id: str, source: str,
                   now: datetime | None = None) -> None:
    """Cora just wrote the field that makes a GHL workflow send an `email` / `sms`. Best-effort: never raises."""
    if channel not in ("email", "sms") or not contact_id:
        return
    try:
        with session.begin_nested():            # savepoint: a failure here must not poison the caller's transaction
            session.execute(
                text("""INSERT INTO channel_events (id, kind, channel, contact_id, source, event_at, checked_at)
                        VALUES (:id, 'handoff', :ch, :c, :src, :at, :at)"""),
                {"id": str(uuid.uuid4()), "ch": channel, "c": contact_id, "src": source,
                 "at": now or datetime.now(tz=timezone.utc)})
    except Exception as exc:                      # monitoring must never break sending
        logger.error("record_handoff failed: %s", exc)


# ── per-channel numbers ──────────────────────────────────────────────────────

_MSG_ROWS = """
    SELECT h.event_at,
           (SELECT d.outcome FROM channel_events d
             WHERE d.kind = 'delivery' AND d.channel = h.channel AND d.contact_id = h.contact_id
               AND d.event_at BETWEEN h.event_at - interval '2 minutes' AND h.event_at + interval '60 minutes'
             ORDER BY (d.outcome = 'delivered') DESC, (d.outcome = 'failed') DESC, d.event_at LIMIT 1) AS outcome_seen,
           h.contact_id, (h.detail->>'no_address') AS no_address
    FROM channel_events h
    WHERE h.kind = 'handoff' AND h.channel = :ch AND h.event_at >= :since
"""

_CALL_ROWS = f"""
    SELECT j.updated_at AS event_at,
           (SELECT CASE WHEN {CALL_OK_SQL} THEN 'delivered' ELSE 'failed' END
              FROM call_events c
             WHERE c.contact_id = {CALL_JOB_CONTACT}
               AND c.created_at BETWEEN j.updated_at - interval '2 minutes' AND j.updated_at + interval '90 minutes'
             ORDER BY c.created_at LIMIT 1) AS outcome,
           {CALL_JOB_CONTACT} AS contact_id
    FROM scheduled_jobs j
    WHERE j.job_type = 'launch_outbound_call' AND j.status = 'completed' AND j.updated_at >= :since
"""


def _rows(session: Session, channel: str, since: datetime) -> list[tuple[datetime, str | None, str]]:
    sql = _CALL_ROWS if channel == "call" else _MSG_ROWS
    params: dict[str, Any] = {"since": since}
    if channel != "call":
        params["ch"] = channel
    out = []
    for r in session.execute(text(sql), params).fetchall():
        outcome = r[1]
        if channel != "call" and outcome is None and len(r) > 3 and r[3] == "true":
            outcome = ch.NO_ADDRESS                       # no email address in GHL (see mark_no_address)
        out.append((_aware(r[0]), outcome, r[2]))
    return out


def _aware(v: Any) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _tally(rows: list[tuple[datetime, str | None, str]], now: datetime, confirm_min: int) -> dict[str, int]:
    out = {"sent": len(rows), "delivered": 0, "failed": 0, "unconfirmed": 0, "pending": 0, "no_address": 0}
    for at, outcome, _ in rows:
        if outcome == ch.NO_ADDRESS:                      # could never be sent: not a delivery problem
            out["no_address"] += 1
            out["sent"] -= 1
        elif outcome == ch.DELIVERED:
            out["delivered"] += 1
        elif outcome == ch.FAILED:
            out["failed"] += 1
        elif now - at >= timedelta(minutes=confirm_min):
            out["unconfirmed"] += 1
        else:
            out["pending"] += 1
    return out


def _last_delivered(session: Session, channel: str) -> datetime | None:
    if channel == "call":
        v = session.execute(text(f"SELECT max(c.created_at) FROM call_events c WHERE lower(coalesce(c.direction,'')) LIKE '%outbound%' AND {CALL_OK_SQL}")).scalar()
    else:
        v = session.execute(
            text("SELECT max(event_at) FROM channel_events WHERE kind='delivery' AND channel=:c AND outcome='delivered'"),
            {"c": channel}).scalar()
    return _aware(v) if v else None


def _muted(session: Session, settings: Any, channel: str) -> str | None:
    muted = {x.strip().lower() for x in _cfg(session, settings, "channel_silence_muted", "").split(",") if x.strip()}
    if channel in muted:
        return "muted by operator (channel_silence_muted)"
    try:
        from app.core.mode_flags import get_mode_flags

        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            return "system paused on purpose"
        if channel == "call" and flags.outbound_campaigns_paused:
            return "outbound campaigns paused on purpose"
    except Exception as exc:
        logger.warning("channel_health: mode flags unreadable: %s", exc)
    return None


def channel_stats(session: Session, settings: Any, channel: str, now: datetime) -> ch.ChannelStats:
    from app.services.delivery_sync import tracker_age_hours

    th = thresholds(session, settings)
    confirm = 25 if channel == "call" else th.confirm_minutes
    rows = _rows(session, channel, now - timedelta(hours=24))
    t = _tally(rows, now, confirm)
    stats = ch.ChannelStats(
        channel=channel, sent=t["sent"], delivered=t["delivered"], failed=t["failed"], unconfirmed=t["unconfirmed"],
        no_address=t["no_address"], last_delivered_at=_last_delivered(session, channel),
        last_handoff_at=max((r[0] for r in rows if r[1] != ch.NO_ADDRESS), default=None),
        muted_reason=_muted(session, settings, channel))
    if channel != "call":
        stats.ghl_delivered_24h = int(session.execute(
            text("SELECT count(*) FROM channel_events WHERE kind='delivery' AND channel=:c AND outcome='delivered' AND event_at >= :s"),
            {"c": channel, "s": now - timedelta(hours=24)}).scalar() or 0)
    return ch.evaluate(stats, now, th, tracker_age_hours(session, now))


def trend(session: Session, channel: str, now: datetime, days: int = 7) -> list[dict[str, Any]]:
    rows = _rows(session, channel, now - timedelta(days=days))
    buckets: dict[str, dict[str, int]] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).date().isoformat()
        buckets[d] = {"sent": 0, "delivered": 0}
    for at, outcome, _ in rows:
        if outcome == ch.NO_ADDRESS:
            continue
        d = at.date().isoformat()
        if d in buckets:
            buckets[d]["sent"] += 1
            buckets[d]["delivered"] += 1 if outcome == ch.DELIVERED else 0
    return [{"day": d, **v} for d, v in buckets.items()]


def replies(session: Session, now: datetime) -> dict[str, Any]:
    by = {r[0]: (int(r[1]), r[2]) for r in session.execute(text(
        "SELECT channel, count(*) FILTER (WHERE event_at >= :s), max(event_at) FROM channel_events "
        "WHERE kind='reply' GROUP BY channel"), {"s": now - timedelta(hours=24)}).fetchall()}
    last = max((_aware(v[1]) for v in by.values() if v[1]), default=None)
    return {"last_24h": sum(v[0] for v in by.values()), "last_at": last.isoformat() if last else None,
            "by_channel": {k: v[0] for k, v in by.items()}}


def snapshot(session: Session, settings: Any, now: datetime | None = None) -> dict[str, Any]:
    from app.services.delivery_sync import _state

    now = now or datetime.now(tz=timezone.utc)
    out = []
    for c in ch.CHANNELS:
        s = channel_stats(session, settings, c, now)
        out.append({
            "channel": c, "label": ch.LABEL[c], "level": s.level, "reasons": s.reasons,
            "sent": s.sent, "delivered": s.delivered, "failed": s.failed, "unconfirmed": s.unconfirmed,
            "no_address": s.no_address, "rate": s.rate, "ghl_delivered_24h": s.ghl_delivered_24h,
            "last_delivered_at": s.last_delivered_at.isoformat() if s.last_delivered_at else None,
            "last_handoff_at": s.last_handoff_at.isoformat() if s.last_handoff_at else None,
            "muted": s.muted_reason, "trend": trend(session, c, now),
        })
    order = {ch.RED: 3, ch.AMBER: 2, ch.GREEN: 1, ch.GREY: 0}
    worst = max((x["level"] for x in out), key=lambda lv: order[lv], default=ch.GREEN)
    st = _state(session)
    th = thresholds(session, settings)
    return {"channels": out, "replies": replies(session, now), "worst": worst,
            "silence_hours": th.silence_hours, "confirm_minutes": th.confirm_minutes,
            "sync": {"last_run_at": st.get("last_run_at"), "last_error": st.get("last_error"),
                     "backfill_done": bool(st.get("backfill_done"))}}


def _enrich_contacts(items: list[dict[str, Any]], ghl: Any, budget_seconds: float = 12.0) -> None:
    """Add the lead's full phone and email (and GHL id) so an operator can look the lead up in GHL.
    Contact ids that are already phone numbers need no lookup. Parallel, time-boxed; failures leave the fields blank."""
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def looks_phone(v: str) -> bool:
        d = v.replace("+", "").replace("-", "").replace(" ", "")
        return d.isdigit() and len(d) >= 10

    started = time.monotonic()
    ids = sorted({i["contact_id"] for i in items if i["contact_id"] and not looks_phone(i["contact_id"])})
    found: dict[str, dict[str, str]] = {}

    def fetch(cid: str) -> tuple[str, dict[str, str]]:
        rec = ghl.get_contact(cid)
        c = rec.get("contact", rec) if isinstance(rec, dict) else {}
        return cid, {"phone": c.get("phone") or "", "email": c.get("email") or ""}

    if ghl is not None and ids:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(fetch, c) for c in ids[:40]]
            for f in as_completed(futures, timeout=budget_seconds + 5):
                try:
                    cid, info = f.result(timeout=max(budget_seconds - (time.monotonic() - started), 0.1))
                    found[cid] = info
                except Exception:
                    continue
    for it in items:
        cid = it["contact_id"]
        info = found.get(cid, {})
        it["phone"] = cid if looks_phone(cid) else info.get("phone", "")
        it["email"] = info.get("email", "")


def detail(session: Session, channel: str, now: datetime | None = None, limit: int = 50,
           ghl: Any = None) -> dict[str, Any]:
    """Failed + unconfirmed hand-offs of the last 24 h, with the lead's phone / email for GHL look-ups."""
    now = now or datetime.now(tz=timezone.utc)
    rows = sorted(_rows(session, channel, now - timedelta(hours=24)), key=lambda r: r[0], reverse=True)
    confirm = 25 if channel == "call" else 15
    items = []
    for at, outcome, contact in rows:
        if outcome == ch.DELIVERED:
            continue
        if outcome is None and now - at < timedelta(minutes=confirm):
            continue
        err = None
        if channel != "call":
            err = session.execute(text(
                "SELECT error FROM channel_events WHERE kind='delivery' AND channel=:c AND contact_id=:x "
                "AND outcome='failed' AND event_at >= :a ORDER BY event_at LIMIT 1"),
                {"c": channel, "x": contact, "a": at - timedelta(minutes=2)}).scalar()
        items.append({"at": at.isoformat(), "contact_id": contact,
                      "state": ("failed" if outcome == ch.FAILED
                                else "no email on file" if outcome == ch.NO_ADDRESS else "not confirmed"),
                      "error": err})
        if len(items) >= limit:
            break
    try:
        _enrich_contacts(items, ghl)
    except Exception as exc:                       # the list is still useful with ids only
        logger.warning("delivery detail: contact lookup failed: %s", exc)
    return {"channel": channel, "items": items}


# ── scheduled silence check (not an error handler) ───────────────────────────

def run_silence_check(session: Session, settings: Any, now: datetime | None = None,
                      force: bool = False) -> dict[str, str]:
    """Hourly: evaluate each channel; raise / resolve one alert_events row per channel and email
    (to alert_email_to, cc Ali) once when it fires and once when it recovers."""
    from app.models.alert_event import AlertEvent
    from app.services.alerting import _smtp_send

    now = now or datetime.now(tz=timezone.utc)
    if not force:
        last = session.execute(text("SELECT max(created_at) FROM audit_log WHERE action = 'channel_silence_check'")).scalar()
        if last and (now - _aware(last)) < timedelta(minutes=55):
            return {}
    session.execute(
        text("""INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'channel_health', 'all', 'channel_silence_check', 'system', '{}'::jsonb, :n)"""),
        {"id": str(uuid.uuid4()), "n": now})
    cc = [x.strip() for x in _cfg(session, settings, "channel_silence_cc", DEFAULT_CC).split(",") if x.strip()]
    to = [x.strip() for x in (getattr(settings, "alert_email_to", "") or "").split(",") if x.strip()]
    result: dict[str, str] = {}
    for c in ch.CHANNELS:
        s = channel_stats(session, settings, c, now)
        result[c] = s.level
        a_type = ALERT_PREFIX + c
        active = session.execute(
            text("SELECT id, email_sent_at FROM alert_events WHERE alert_type = :t AND status = 'active' LIMIT 1"),
            {"t": a_type}).fetchone()
        firing = s.level == ch.RED
        if firing and not active:
            msg = f"{ch.LABEL[c]}: " + "; ".join(s.reasons)
            session.add(AlertEvent(id=str(uuid.uuid4()), alert_type=a_type, severity="critical", status="active",
                                   message=msg, email_sent_at=now, last_seen_at=now, created_at=now))
            _smtp_send(settings, to, alert_subject(c, "active"),
                       _body(c, s, now, "ACTIVE"), log_label=a_type, cc_addrs=cc,
                       extra_headers=alert_headers(c, "active"))
        elif firing and active:
            session.execute(text("UPDATE alert_events SET last_seen_at = :n WHERE id = :i"), {"n": now, "i": active[0]})
        elif active:
            session.execute(text("UPDATE alert_events SET status='resolved', resolved_at=:n WHERE id=:i"),
                            {"n": now, "i": active[0]})
            if active[1]:
                _smtp_send(settings, to, alert_subject(c, "resolved"),
                           _body(c, s, now, "RESOLVED"), log_label=a_type, cc_addrs=cc,
                           extra_headers=alert_headers(c, "resolved"))
    return result


def alert_subject(channel: str, state: str) -> str:
    """Fixed subject shapes (rules are pinned to them): "[CRITICAL] Cora: SMS has gone quiet" /
    "[RESOLVED] Cora: SMS is delivering again"; "Calls" is plural (have / are)."""
    plural = channel == "call"
    if state == "active":
        return f"[CRITICAL] Cora: {ch.LABEL[channel]} {'have' if plural else 'has'} gone quiet"
    return f"[RESOLVED] Cora: {ch.LABEL[channel]} {'are' if plural else 'is'} delivering again"


def alert_headers(channel: str, state: str) -> dict[str, str]:
    """Hidden labels on every delivery-health alert so a mail rule (e.g. Ali's inbox classifier) can recognise it
    exactly, whatever the subject says. Keep these names stable - rules are pinned to them."""
    return {"X-Cora-Alert": "health", "X-Cora-Alert-Channel": channel, "X-Cora-Alert-State": state,
            "X-Priority": "1" if state == "active" else "3", "Importance": "high" if state == "active" else "normal"}


def _body(channel: str, s: ch.ChannelStats, now: datetime, state: str) -> str:
    last = s.last_delivered_at.isoformat() if s.last_delivered_at else "never seen"
    return "\n".join([
        f"{ch.LABEL[channel]} delivery health: {state}",
        "Why: " + ("; ".join(s.reasons) or "back to normal"),
        f"Last confirmed delivery: {last}",
        f"Last 24h - handed over: {s.sent}, delivered: {s.delivered}, failed: {s.failed}, not confirmed: {s.unconfirmed}",
        f"Checked: {now.isoformat()}",
        "",
        "Measured on DELIVERED (GHL/provider status, call logs), per channel. Open the Delivery Health tile for detail.",
    ])


# ── leads with no email address in GHL ───────────────────────────────────────

def mark_no_address(session: Session, ghl: Any, now: datetime | None = None, confirm_minutes: int = 15,
                    max_lookups: int = 15) -> dict[str, int]:
    """An email hand-off that GHL never confirms is often not a delivery failure at all: the lead has no email on the
    GHL contact, so there was nothing to send to. Look each such contact up ONCE and remember the answer on the
    hand-off row (detail.address_checked / detail.no_address) so the tile and the alert can tell the two apart.
    Best-effort and bounded; a failed lookup is simply retried on the next run."""
    import json

    from app.services.wrong_date_monitor import _lookup_contact

    now = now or datetime.now(tz=timezone.utc)
    rows = session.execute(text("""
        SELECT h.id, h.contact_id FROM channel_events h
        WHERE h.kind = 'handoff' AND h.channel = 'email'
          AND h.event_at BETWEEN :since AND :cut
          AND coalesce(h.detail->>'address_checked', '') = ''
          AND NOT EXISTS (SELECT 1 FROM channel_events d
                          WHERE d.kind = 'delivery' AND d.channel = 'email' AND d.contact_id = h.contact_id
                            AND d.event_at BETWEEN h.event_at - interval '2 minutes' AND h.event_at + interval '60 minutes')
        ORDER BY h.event_at DESC LIMIT :n"""),
        {"since": now - timedelta(days=7), "cut": now - timedelta(minutes=confirm_minutes), "n": max_lookups}).fetchall()
    out = {"checked": 0, "no_address": 0, "errors": 0}
    seen: dict[str, bool] = {}
    for hid, cid in rows:
        try:
            if cid not in seen:
                _, rec = _lookup_contact(ghl, cid)
                c = rec.get("contact", rec) if isinstance(rec, dict) else {}
                seen[cid] = not (c.get("email") or "").strip()
            missing = seen[cid]
        except Exception as exc:
            out["errors"] += 1
            logger.warning("mark_no_address: lookup failed for %s: %s", cid, exc)
            continue
        session.execute(
            text("""UPDATE channel_events SET detail = coalesce(detail, CAST('{}' AS jsonb)) || CAST(:d AS jsonb) WHERE id = :i"""),
            {"i": hid, "d": json.dumps({"address_checked": True, "no_address": "true" if missing else "false"})})
        out["checked"] += 1
        out["no_address"] += 1 if missing else 0
    return out
