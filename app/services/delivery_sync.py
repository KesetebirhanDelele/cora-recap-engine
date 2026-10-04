"""
GHL delivery + reply sync (spec/35).

Reads GHL conversations (conversations-scoped token) and records, in `channel_events`:
  * kind 'delivery' - every OUTBOUND email / SMS with GHL's provider status (delivered / undelivered...)
  * kind 'reply'    - every INBOUND SMS / email / call; SMS + email replies also land in `inbound_messages`
                      (which until now was never written, so STOP replies were invisible to Cora)

Throttled (default every 5 min) and time-boxed so it can ride the 60 s metrics cycle. First run backfills
`delivery_sync_backfill_hours` (72) in time-boxed slices, resuming from a stored cursor. Never writes to GHL.
Calls are NOT read here - their outcome comes from call_events (Synthflow logs reach Cora ~10-15 min later).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import channel_health as ch

logger = logging.getLogger(__name__)

TYPE_MAP = {"TYPE_SMS": "sms", "TYPE_EMAIL": "email", "TYPE_CALL": "call", "TYPE_IVR_CALL": "call"}
SYNC_KEY = "delivery_sync"
RUN_BUDGET_SECONDS = 24.0
MAX_CONVERSATIONS_PER_RUN = 80
PAGE = 30
_monotonic = time.monotonic


class _RateLimiter:
    """At most `rate` GHL calls per second across all threads (GHL answers 429 above its burst limit, and Cora's
    other jobs share the same location quota)."""

    def __init__(self, rate: float) -> None:
        import threading

        self._gap = 1.0 / rate
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._gap
        if slot > now:
            time.sleep(slot - now)


GHL_CALLS_PER_SECOND = 5.0
_limiter = _RateLimiter(GHL_CALLS_PER_SECOND)


def _cfg(session: Session, settings: Any, key: str, default: str) -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


def _state(session: Session) -> dict:
    row = session.execute(text("SELECT value FROM channel_sync_state WHERE key = :k"), {"k": SYNC_KEY}).fetchone()
    v = row[0] if row else {}
    return json.loads(v) if isinstance(v, str) else dict(v or {})


def _save_state(session: Session, state: dict, now: datetime) -> None:
    session.execute(
        text("""INSERT INTO channel_sync_state (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), :n)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at"""),
        {"k": SYNC_KEY, "v": json.dumps(state), "n": now})


def tracker_age_hours(session: Session, now: datetime) -> float:
    """How long delivery data has existed (first successful sync) - silence is not judged before it covers a window."""
    st = _state(session)
    first = _parse_ts(st.get("first_sync_at"))
    if not first:
        return 0.0
    age = (now - first).total_seconds() / 3600
    # once the backfill finished, GHL history already covers the whole backfill window
    return age + float(st.get("backfill_hours", 72)) if st.get("backfill_done") else age


def _parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _upsert_event(session: Session, *, kind: str, channel: str, contact_id: str, external_id: str,
                  status_raw: str | None, outcome: str | None, error: str | None, event_at: datetime,
                  now: datetime, source: str = "ghl_sync", detail: dict | None = None) -> bool:
    """Insert or refresh by (kind, channel, external_id). True when a row was inserted/changed."""
    res = session.execute(
        text("""
            INSERT INTO channel_events
                (id, kind, channel, contact_id, external_id, status_raw, outcome, error, source, event_at, checked_at, detail)
            VALUES (:id, :k, :ch, :c, :x, :sr, :o, :e, :src, :at, :now, CAST(:d AS jsonb))
            ON CONFLICT (kind, channel, external_id) WHERE external_id IS NOT NULL DO UPDATE
               SET status_raw = EXCLUDED.status_raw, outcome = EXCLUDED.outcome, error = EXCLUDED.error,
                   checked_at = EXCLUDED.checked_at
               WHERE channel_events.outcome IS DISTINCT FROM EXCLUDED.outcome
                  OR channel_events.status_raw IS DISTINCT FROM EXCLUDED.status_raw
        """),
        {"id": str(uuid.uuid4()), "k": kind, "ch": channel, "c": contact_id, "x": external_id, "sr": status_raw,
         "o": outcome, "e": error, "src": source, "at": event_at, "now": now,
         "d": json.dumps(detail) if detail else None})
    return (res.rowcount or 0) > 0


def _record_reply(session: Session, channel: str, m: dict, contact_id: str, at: datetime, now: datetime,
                  ghl: Any = None, settings: Any = None, llm_budget: list[int] | None = None) -> None:
    if channel in ("email", "sms"):
        from app.core import optout as _oo

        if _oo.is_own_echo(m.get("body")):          # Cora's own email / footer logged back as "inbound" - not a reply
            return
    inserted = _upsert_event(
        session, kind="reply", channel=channel, contact_id=contact_id, external_id=m["id"],
        status_raw=m.get("status"), outcome=None, error=None, event_at=at, now=now,
        detail={"type": m.get("messageType")})
    if inserted and channel in ("sms", "email"):
        session.execute(
            text("""INSERT INTO inbound_messages (id, contact_id, channel, body, received_at, external_id)
                    VALUES (:id, :c, :ch, :b, :at, :x) ON CONFLICT (external_id) WHERE external_id IS NOT NULL DO NOTHING"""),
            {"id": str(uuid.uuid4()), "c": contact_id, "ch": channel, "b": (m.get("body") or "")[:2000],
             "at": at, "x": m["id"]})
        # Opt-out wording in the reply -> GHL DND by the lead's own words (spec/36). Never breaks the sync.
        if settings is not None:
            from app.services import optout

            # ghl=None on purpose: `ghl` here is the conversations-scoped token (read-only for contacts); the DND
            # write must use the contacts token, which optout builds itself.
            optout.handle_reply(session, settings, None, channel=channel, message=m, contact_id=contact_id,
                                llm_budget=llm_budget)


def _prefetch(session: Session, ghl: Any, convs: list[dict], since: datetime) -> tuple[dict, dict, set]:
    """Fetch the message lists of a page of conversations - and the provider status of every email that is not yet
    final - in parallel (the GHL calls are the slow part; a day's call logging makes hundreds of conversations
    active). Returns ({conv_id: messages}, {message_id: email_status}, {final email message ids})."""
    from concurrent.futures import ThreadPoolExecutor

    def msgs_of(conv: dict) -> list[dict]:
        try:
            _limiter.wait()
            return ghl.get_conversation_messages(conv["id"], limit=30)
        except Exception as exc:
            logger.warning("delivery_sync: messages fetch failed: %s", exc)
            return []

    wanted = [c for c in convs if (_parse_ts(c.get("lastMessageDate")) or since) >= since]
    with ThreadPoolExecutor(max_workers=8) as pool:
        lists = list(pool.map(msgs_of, wanted))
    by_conv = {c["id"]: m for c, m in zip(wanted, lists)}

    emails: dict[str, str] = {}
    for msgs in by_conv.values():
        for m in msgs:
            at = _parse_ts(m.get("dateAdded"))
            if m.get("messageType") == "TYPE_EMAIL" and (m.get("direction") or "").lower() == "outbound" \
                    and m.get("id") and at is not None and at >= since:
                ids = (((m.get("meta") or {}).get("email") or {}).get("messageIds")) or []
                if ids:
                    emails[m["id"]] = ids[0]
    final: set[str] = set()
    if emails:
        final = {r[0] for r in session.execute(
            text("SELECT external_id FROM channel_events WHERE kind='delivery' AND channel='email' "
                 "AND outcome IN ('delivered','failed') AND external_id = ANY(:ids)"), {"ids": list(emails)}).fetchall()}
    todo = {mid: pid for mid, pid in emails.items() if mid not in final}

    def status_of(item: tuple[str, str]) -> tuple[str, str | None]:
        try:
            _limiter.wait()
            return item[0], ghl.get_email_status(item[1])
        except Exception as exc:
            logger.warning("delivery_sync: email status lookup failed: %s", exc)
            return item[0], None

    statuses: dict[str, str | None] = {}
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses = dict(pool.map(status_of, todo.items()))
    return by_conv, statuses, final


def _flag_content(session: Session, settings: Any, m: dict, channel: str, cid: str, at: datetime, now: datetime) -> None:
    """Record outbound text / email that names a retired course or makes an employment claim (content audit). Never raises."""
    try:
        from app.core import content_audit as ca
        from app.core import offer
        from app.core.app_config import get_config_value

        raw = get_config_value("offer_forbidden_terms", session, settings, None) if settings is not None else None
        hits = ca.forbidden_in(m.get("body"), offer.parse_terms(raw))
        if hits:
            _upsert_event(session, kind="content_flag", channel=channel, contact_id=cid, external_id=m["id"],
                          status_raw=None, outcome=None, error=", ".join(hits)[:290], event_at=at, now=now,
                          source=str(m.get("source") or "?")[:30],
                          detail={"terms": hits, "snippet": (m.get("body") or "")[:160]})
    except Exception as exc:
        logger.warning("delivery_sync: content audit failed: %s", exc)


def _flag_opt_out_answers(session: Session, settings: Any, conv: dict, msgs: list[dict], now: datetime) -> None:
    try:
        from app.core import content_audit as ca

        cid = conv.get("contactId") or ""
        for m in ca.opt_out_answers(msgs, _parse_ts):
            _upsert_event(session, kind="content_flag", channel="sms", contact_id=m.get("contactId") or cid, external_id=m["id"],
                          status_raw=None, outcome=None, error="answered_opt_out", event_at=_parse_ts(m.get("dateAdded")) or now,
                          now=now, source=str(m.get("source") or "?")[:30],
                          detail={"terms": ["answered_opt_out"], "snippet": (m.get("body") or "")[:160]})
    except Exception as exc:
        logger.warning("delivery_sync: opt-out answer audit failed: %s", exc)


def _process_conversation(session: Session, conv: dict, msgs: list[dict], email_status: dict, final_emails: set,
                          since: datetime, now: datetime, stats: dict, settings: Any = None,
                          llm_budget: list[int] | None = None) -> None:
    stats["conversations"] += 1
    contact_id = conv.get("contactId") or ""
    _flag_opt_out_answers(session, settings, conv, msgs, now)
    for m in msgs:
        channel = TYPE_MAP.get(m.get("messageType") or m.get("type") or "")
        at = _parse_ts(m.get("dateAdded"))
        if not channel or at is None or at < since or not m.get("id"):
            continue
        cid = m.get("contactId") or contact_id
        direction = (m.get("direction") or "").lower()
        if direction == "inbound":
            _record_reply(session, channel, m, cid, at, now, None, settings, llm_budget)
            stats["replies"] += 1
            continue
        if channel == "call" or direction != "outbound":
            continue                                   # outbound call outcome = call_events, not GHL
        _flag_content(session, settings, m, channel, cid, at, now)
        if channel == "sms":
            status = m.get("status")
            err = ch.extract_error(m) if ch.sms_outcome(status) == ch.FAILED else None
            _upsert_event(session, kind="delivery", channel="sms", contact_id=cid, external_id=m["id"],
                          status_raw=status, outcome=ch.sms_outcome(status), error=err, event_at=at, now=now)
            stats["deliveries"] += 1
        else:                                          # email: status is behind the provider message id
            if m["id"] in final_emails:
                continue
            status = email_status.get(m["id"])
            _upsert_event(session, kind="delivery", channel="email", contact_id=cid, external_id=m["id"],
                          status_raw=status, outcome=ch.email_outcome(status), error=None, event_at=at, now=now)
            stats["deliveries"] += 1


def _walk(session: Session, ghl: Any, settings: Any, *, start_after: int | None, cutoff: datetime, now: datetime,
          stats: dict, deadline: float, max_convs: int, llm_budget: list[int]) -> tuple[bool, int | None, datetime | None]:
    """Walk conversations newest -> oldest until one is older than `cutoff`, `max_convs` were handled or the deadline
    passed. Returns (reached_end, resume_cursor, newest_activity_seen). resume_cursor = lastMessageDate (ms) of the
    last conversation fully handled."""
    handled = 0
    cursor_done: int | None = start_after
    newest: datetime | None = None
    cursor = start_after
    while handled == 0 or (handled < max_convs and _monotonic() < deadline):     # always at least one page: progress every run
        convs = ghl.search_conversations(sort_by="last_message_date", sort="desc", start_after_date=cursor, limit=PAGE)
        if not convs:
            return True, cursor_done, newest
        by_conv, email_status, final_emails = _prefetch(session, ghl, convs, cutoff)
        for conv in convs:
            lm = _parse_ts(conv.get("lastMessageDate"))
            if lm is not None and lm < cutoff:
                return True, cursor_done, newest
            if lm is not None and (newest is None or lm > newest):
                newest = lm
            try:
                _process_conversation(session, conv, by_conv.get(conv["id"], []), email_status, final_emails, cutoff,
                                      now, stats, settings, llm_budget)
            except Exception as exc:
                stats["errors"] += 1
                logger.warning("delivery_sync: conversation failed: %s", exc)
            handled += 1
            ts = conv.get("lastMessageDate")
            if isinstance(ts, (int, float)):
                cursor_done = int(ts)
        # the whole prefetched page is handled (processing is local and fast); limits apply between pages
        if handled >= max_convs or _monotonic() >= deadline:
            return False, cursor_done, newest
        last_ts = convs[-1].get("lastMessageDate")
        if not isinstance(last_ts, (int, float)):
            return True, cursor_done, newest
        cursor = int(last_ts)
    return False, cursor_done, newest


def sync(session: Session, settings: Any, now: datetime | None = None, force: bool = False,
         ghl: Any = None) -> dict[str, Any]:
    """One throttled, time-boxed pass. Safe to call every cycle.

    Every run does TWO jobs, in this order, so fresh messages are never starved by the history load:
      1. forward  - conversations active since (watermark - 30 min): new deliveries / replies show up within one run
      2. backfill - resumes the one-time walk back to `now - backfill_hours` from a stored cursor, until finished
    State (channel_sync_state 'delivery_sync'): watermark, backfill_done, backfill_cursor, first_sync_at, last_run_at.
    """
    now = now or datetime.now(tz=timezone.utc)
    state = _state(session)
    every = float(_cfg(session, settings, "delivery_sync_interval_seconds", "120"))
    last = _parse_ts(state.get("last_run_at"))
    if not force and last and (now - last).total_seconds() < every:
        return {"skipped": "throttled"}
    if ghl is None:
        if not getattr(settings, "ghl_conversations_api_key", None):
            return {"skipped": "no conversations token"}
        from app.adapters.ghl import GHLClient

        ghl = GHLClient(settings=settings, api_key_override=settings.ghl_conversations_api_key)

    backfill_h = float(_cfg(session, settings, "delivery_sync_backfill_hours", "72"))
    horizon = now - timedelta(hours=backfill_h)
    watermark = _parse_ts(state.get("watermark"))
    fwd_cutoff = max((watermark or (now - timedelta(minutes=30))) - timedelta(minutes=30), horizon)
    started = _monotonic()
    deadline = started + RUN_BUDGET_SECONDS
    stats = {"conversations": 0, "deliveries": 0, "replies": 0, "out_of_time": False, "errors": 0}
    llm_budget = [8]                   # at most 8 LLM opt-out judgements per run
    new_watermark = watermark
    try:
        # 1. forward: always first. A pass that cannot finish in one run resumes from its cursor next run
        #    (a day's call logging makes hundreds of conversations active; GHL costs ~0.75 s per conversation).
        resume = state.get("fwd_resume") or None
        if resume:
            f_start, f_cutoff, pass_started = int(resume["cursor"]), _parse_ts(resume["cutoff"]), _parse_ts(resume["started"])
        else:
            f_start, f_cutoff, pass_started = None, fwd_cutoff, now
        done_fwd, f_cursor, _ = _walk(session, ghl, settings, start_after=f_start, cutoff=f_cutoff, now=now, stats=stats,
                                      deadline=started + RUN_BUDGET_SECONDS * 0.6, max_convs=400, llm_budget=llm_budget)
        session.commit()
        stats["forward_complete"] = done_fwd
        if done_fwd:                    # everything newer than the cutoff was handled
            new_watermark = pass_started - timedelta(minutes=1)
            state["fwd_resume"] = None
        else:
            state["fwd_resume"] = {"cursor": f_cursor, "cutoff": f_cutoff.isoformat(), "started": pass_started.isoformat()}
        # 2. backfill with whatever budget is left
        if not state.get("backfill_done") and _monotonic() < deadline:
            start = int(state["backfill_cursor"]) if state.get("backfill_cursor") else None
            done_bf, cursor, _ = _walk(session, ghl, settings, start_after=start, cutoff=horizon, now=now, stats=stats,
                                       deadline=deadline, max_convs=MAX_CONVERSATIONS_PER_RUN, llm_budget=llm_budget)
            session.commit()
            state["backfill_cursor"] = None if done_bf else cursor
            if done_bf:
                state["backfill_done"] = True
    except Exception as exc:
        session.rollback()
        logger.error("delivery_sync failed: %s", exc)
        state.update(last_run_at=now.isoformat(), last_error=str(exc)[:200])
        _save_state(session, state, now)
        session.commit()
        return {"error": str(exc)}

    state.update(last_run_at=now.isoformat(), last_error=None, stats=stats, backfill_hours=backfill_h,
                 watermark=(new_watermark or now).isoformat())
    state.setdefault("first_sync_at", now.isoformat())
    _save_state(session, state, now)
    session.commit()
    return stats
