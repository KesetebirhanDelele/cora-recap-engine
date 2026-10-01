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
RUN_BUDGET_SECONDS = 20.0
MAX_CONVERSATIONS_PER_RUN = 80
PAGE = 50
_monotonic = time.monotonic


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


def _is_final(session: Session, channel: str, external_id: str) -> bool:
    row = session.execute(
        text("SELECT outcome FROM channel_events WHERE kind='delivery' AND channel=:c AND external_id=:x"),
        {"c": channel, "x": external_id}).fetchone()
    return bool(row and row[0] in (ch.DELIVERED, ch.FAILED))


def _record_reply(session: Session, channel: str, m: dict, contact_id: str, at: datetime, now: datetime,
                  ghl: Any = None, settings: Any = None, llm_budget: list[int] | None = None) -> None:
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


def _process_conversation(session: Session, ghl: Any, conv: dict, since: datetime, now: datetime,
                          stats: dict, deadline: float, settings: Any = None,
                          llm_budget: list[int] | None = None) -> None:
    msgs = ghl.get_conversation_messages(conv["id"], limit=30)
    stats["conversations"] += 1
    contact_id = conv.get("contactId") or ""
    for m in msgs:
        channel = TYPE_MAP.get(m.get("messageType") or m.get("type") or "")
        at = _parse_ts(m.get("dateAdded"))
        if not channel or at is None or at < since or not m.get("id"):
            continue
        cid = m.get("contactId") or contact_id
        direction = (m.get("direction") or "").lower()
        if direction == "inbound":
            _record_reply(session, channel, m, cid, at, now, ghl, settings, llm_budget)
            stats["replies"] += 1
            continue
        if channel == "call" or direction != "outbound":
            continue                                   # outbound call outcome = call_events, not GHL
        if channel == "sms":
            status = m.get("status")
            err = ch.extract_error(m) if ch.sms_outcome(status) == ch.FAILED else None
            _upsert_event(session, kind="delivery", channel="sms", contact_id=cid, external_id=m["id"],
                          status_raw=status, outcome=ch.sms_outcome(status), error=err, event_at=at, now=now)
            stats["deliveries"] += 1
        else:                                          # email: status is behind the provider message id
            if _is_final(session, "email", m["id"]):
                continue
            if _monotonic() > deadline:
                stats["out_of_time"] = True
                continue
            ids = (((m.get("meta") or {}).get("email") or {}).get("messageIds")) or []
            status = None
            if ids:
                try:
                    status = ghl.get_email_status(ids[0])
                except Exception as exc:
                    logger.warning("delivery_sync: email status lookup failed: %s", exc)
            _upsert_event(session, kind="delivery", channel="email", contact_id=cid, external_id=m["id"],
                          status_raw=status, outcome=ch.email_outcome(status), error=None, event_at=at, now=now)
            stats["deliveries"] += 1


def sync(session: Session, settings: Any, now: datetime | None = None, force: bool = False,
         ghl: Any = None) -> dict[str, Any]:
    """One throttled, time-boxed pass. Safe to call every cycle.

    State (channel_sync_state 'delivery_sync'): backfill_done, backfill_cursor (lastMessageDate ms to
    resume from), watermark (newest conversation activity seen), first_sync_at, last_run_at.
    backfill phase: walk conversations newest -> oldest down to `now - backfill_hours`, resuming each run.
    forward phase:  walk newest -> (watermark - 30 min); the overlap re-reads statuses that change after sending.
    """
    now = now or datetime.now(tz=timezone.utc)
    state = _state(session)
    every = float(_cfg(session, settings, "delivery_sync_interval_seconds", "300"))
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
    backfill = not state.get("backfill_done")
    watermark = _parse_ts(state.get("watermark"))
    cutoff = horizon if backfill else max((watermark or horizon) - timedelta(minutes=30), horizon)
    start_after: int | None = int(state["backfill_cursor"]) if backfill and state.get("backfill_cursor") else None
    deadline = _monotonic() + RUN_BUDGET_SECONDS
    stats = {"conversations": 0, "deliveries": 0, "replies": 0, "out_of_time": False, "errors": 0}
    newest = watermark or horizon
    llm_budget = [8]                   # at most 8 LLM opt-out judgements per run
    reached_end = False
    try:
        while not reached_end and stats["conversations"] < MAX_CONVERSATIONS_PER_RUN and _monotonic() < deadline:
            convs = ghl.search_conversations(sort_by="last_message_date", sort="desc",
                                             start_after_date=start_after, limit=PAGE)
            if not convs:
                reached_end = True
                break
            interrupted = False
            for conv in convs:
                lm = _parse_ts(conv.get("lastMessageDate"))
                if lm is not None and lm < cutoff:
                    reached_end = True
                    break
                if lm is not None and lm > newest:
                    newest = lm
                try:
                    _process_conversation(session, ghl, conv, cutoff, now, stats, deadline, settings, llm_budget)
                except Exception as exc:
                    stats["errors"] += 1
                    logger.warning("delivery_sync: conversation failed: %s", exc)
                if stats["conversations"] >= MAX_CONVERSATIONS_PER_RUN or _monotonic() >= deadline:
                    interrupted = True
                    break
                if backfill:
                    ts = conv.get("lastMessageDate")
                    if isinstance(ts, (int, float)):
                        state["backfill_cursor"] = int(ts)       # resume AFTER this conversation next run
            if interrupted or reached_end:
                break
            last_ts = convs[-1].get("lastMessageDate")
            if not isinstance(last_ts, (int, float)):
                reached_end = True
                break
            start_after = int(last_ts)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("delivery_sync failed: %s", exc)
        state.update(last_run_at=now.isoformat(), last_error=str(exc)[:200])
        _save_state(session, state, now)
        session.commit()
        return {"error": str(exc)}

    if backfill and reached_end:
        state.update(backfill_done=True, backfill_cursor=None)
    state.update(last_run_at=now.isoformat(), last_error=None, stats=stats, watermark=newest.isoformat(),
                 backfill_hours=backfill_h)
    state.setdefault("first_sync_at", now.isoformat())
    _save_state(session, state, now)
    session.commit()
    return stats
