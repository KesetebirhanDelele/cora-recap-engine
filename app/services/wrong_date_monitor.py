"""
Wrong-date monitor (spec/32).

Scans recently sent SMS/emails for a next-class-start or next-open-house date
that disagrees with the dashboard Settings values, records one incident per
offending message, emails once per incident, and lets an operator send a
correction SMS (via the GHL "Message" custom field, the same path VM
follow-ups use) from the "Wrong Date Monitor" dashboard tile.

Detection logic itself lives in app/core/wrong_date_guard.py (pure, tested).
This module is the I/O around it: DB reads/writes, email, GHL write.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.sms_eligibility import (
    BLOCK,
    HOLD,
    SEND,
    Verdict,
    contact_block_reason,
    cora_state_verdict,
    in_tcpa_hours,
    is_stop_reply,
)
from app.core.wrong_date_guard import KIND_CLASS, find_wrong_dates

logger = logging.getLogger(__name__)

ALERT_TYPE = "wrong_date_message"

# Only messages this recent are scanned each cycle. Wide enough to catch a
# stale date right after Settings are changed, narrow enough that the scan
# stays cheap and old history never floods the inbox.
SCAN_LOOKBACK_HOURS = 24
SCAN_ROW_LIMIT = 2000
# Hard cap on new incidents (and therefore emails) per 60s cycle.
MAX_NEW_INCIDENTS_PER_CYCLE = 25

_SNIPPET_CHARS = 280

# Injectable so tests don't really sleep / wait.
_sleep = time.sleep
_monotonic = time.monotonic
_KIND_LABEL = {KIND_CLASS: "class start", "open_house": "open house"}


def _config(session: Session, settings: Any, key: str, default: str = "") -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


def _expectation(value: str, changed_at: datetime | None, msg_at: datetime) -> str | None:
    """What a message should be checked against for one date kind.
    - a real value -> that value (every message)
    - unset (cleared/expired) -> "" (nothing scheduled) but ONLY for messages sent
      after the setting was cleared; earlier messages were correct when sent
    - never configured -> None (check disabled)"""
    from app.core.schedule_context import is_unset

    if not is_unset(value):
        return value
    if changed_at is not None and msg_at >= changed_at:
        return ""
    return None


def _key_updated_at(session: Session, key: str) -> datetime | None:
    return session.execute(
        text("SELECT updated_at FROM app_config WHERE key = :k"), {"k": key}
    ).scalar()


def expected_dates(session: Session, settings: Any) -> tuple[str, str]:
    """(next_class_start, next_open_house_date) exactly as typed on the dashboard."""
    return (
        _config(session, settings, "next_class_start"),
        _config(session, settings, "next_open_house_date"),
    )


# ── Scan (called every metrics cycle) ────────────────────────────────────────

def scan_wrong_dates(session: Session, settings: Any, now: datetime) -> int:
    """
    Record + email any newly sent message carrying a wrong class/open-house
    date. Returns the number of new incidents. Idempotent: the UNIQUE
    outbound_message_id + ON CONFLICT DO NOTHING means a retry or a second
    worker can never double-record or double-email the same message.
    """
    from app.services.alerting import _send_alert_email

    class_start, open_house = expected_dates(session, settings)
    class_changed = _key_updated_at(session, "next_class_start")
    oh_changed = _key_updated_at(session, "next_open_house_date")
    new_count = 0

    rows = session.execute(
        text("""
            SELECT m.id, m.contact_id, m.channel, m.subject, m.body, m.created_at
            FROM outbound_messages m
            WHERE m.created_at >= :since
              AND m.status NOT IN ('shadow', 'failed')
              AND NOT EXISTS (
                  SELECT 1 FROM wrong_date_incidents w WHERE w.outbound_message_id = m.id
              )
            ORDER BY m.created_at DESC
            LIMIT :lim
        """),
        {"since": now - timedelta(hours=SCAN_LOOKBACK_HOURS), "lim": SCAN_ROW_LIMIT},
    ).fetchall()

    for msg_id, contact_id, channel, subject, body, created_at in rows:
        if new_count >= MAX_NEW_INCIDENTS_PER_CYCLE:
            break
        wrong = find_wrong_dates(
            body, subject,
            expected_class_start=_expectation(class_start, class_changed, created_at),
            expected_open_house=_expectation(open_house, oh_changed, created_at),
        )
        if not wrong:
            continue

        incident_id = str(uuid.uuid4())
        snippet = ((subject + " — ") if subject else "") + (body or "")
        inserted = session.execute(
            text("""
                INSERT INTO wrong_date_incidents
                  (id, outbound_message_id, contact_id, channel, wrong_dates, snippet,
                   expected_class_start, expected_open_house, message_sent_at, status, created_at)
                VALUES
                  (:id, :mid, :cid, :ch, CAST(:wd AS json), :snip,
                   :ecs, :eoh, :sent, 'open', :now)
                ON CONFLICT (outbound_message_id) DO NOTHING
                RETURNING id
            """),
            {
                "id": incident_id, "mid": msg_id, "cid": contact_id, "ch": channel,
                "wd": json.dumps([w.__dict__ for w in wrong]),
                "snip": snippet[:_SNIPPET_CHARS],
                "ecs": class_start, "eoh": open_house, "sent": created_at, "now": now,
            },
        ).fetchone()
        if inserted is None:
            continue  # another worker got there first — it sends the email
        new_count += 1

        detail = "; ".join(
            f"{_KIND_LABEL[w.kind]} date '{w.raw}' (should be {w.expected or 'none - nothing is scheduled'})"
            for w in wrong
        )
        frontend = (getattr(settings, "frontend_url", "") or "").rstrip("/")
        message = (
            f"Lead {contact_id} was sent a {channel.upper()} with a wrong date: {detail}. "
            f"Review and send a correction: {frontend}/wrong-dates"
        )
        try:
            _send_alert_email(
                settings=settings, alert_id=incident_id, alert_type=ALERT_TYPE,
                severity="warning", message=message, now=now,
            )
        except Exception as exc:  # incident stays recorded and visible on the tile
            logger.error("wrong_date: alert email failed | incident=%s: %s", incident_id, exc)

    _sync_aggregate_alert(session, now)
    return new_count


def _sync_aggregate_alert(session: Session, now: datetime) -> None:
    """Keep one active alert_events row while any incident is open (Alerts page).
    No email here — per-incident emails are sent by scan_wrong_dates."""
    from app.models.alert_event import AlertEvent

    open_count = session.execute(
        text("SELECT COUNT(*) FROM wrong_date_incidents WHERE status = 'open'")
    ).scalar() or 0
    active = session.execute(
        text("SELECT id FROM alert_events WHERE alert_type = :t AND status = 'active' LIMIT 1"),
        {"t": ALERT_TYPE},
    ).fetchone()

    if open_count and not active:
        session.add(AlertEvent(
            id=str(uuid.uuid4()), alert_type=ALERT_TYPE, severity="warning",
            status="active", current_value=float(open_count),
            message=f"{open_count} message(s) sent with a wrong class/open-house date — see Wrong Date Monitor",
            email_sent_at=now, last_seen_at=now, created_at=now,
        ))
    elif open_count and active:
        session.execute(
            text("UPDATE alert_events SET last_seen_at = :now, current_value = :n WHERE id = :id"),
            {"now": now, "n": float(open_count), "id": active[0]},
        )
    elif active:
        session.execute(
            text("UPDATE alert_events SET status = 'resolved', resolved_at = :now "
                 "WHERE alert_type = :t AND status = 'active'"),
            {"now": now, "t": ALERT_TYPE},
        )


# ── Dashboard reads ──────────────────────────────────────────────────────────

def list_incidents(session: Session, status: str = "open", limit: int = 100) -> list[dict]:
    rows = session.execute(
        text("""
            SELECT id, contact_id, channel, wrong_dates, snippet, expected_class_start,
                   expected_open_house, message_sent_at, status, resolved_by, resolved_at,
                   correction_text, created_at
            FROM wrong_date_incidents
            WHERE status = :s
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        {"s": status, "lim": limit},
    ).fetchall()
    keys = ["id", "contact_id", "channel", "wrong_dates", "snippet", "expected_class_start",
            "expected_open_house", "message_sent_at", "status", "resolved_by", "resolved_at",
            "correction_text", "created_at"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        for k in ("message_sent_at", "resolved_at", "created_at"):
            d[k] = d[k].isoformat() if d[k] else None
        out.append(d)
    return out


def incident_stats(session: Session, now: datetime | None = None) -> dict[str, int]:
    """Tile metrics: incidents open right now, and closed in the last 24h."""
    now = now or datetime.now(tz=timezone.utc)
    since = now - timedelta(hours=24)
    row = session.execute(
        text("""
            SELECT
              COUNT(*) FILTER (WHERE status = 'open'),
              COUNT(*) FILTER (WHERE status = 'corrected' AND resolved_at >= :s),
              COUNT(*) FILTER (WHERE status = 'dismissed' AND resolved_at >= :s),
              COUNT(*) FILTER (WHERE created_at >= :s)
            FROM wrong_date_incidents
        """),
        {"s": since},
    ).fetchone()
    open_now, corrected, dismissed, new_24h = (int(x or 0) for x in row)
    return {
        "open": open_now,
        "closed_24h": corrected + dismissed,
        "corrected_24h": corrected,
        "dismissed_24h": dismissed,
        "new_24h": new_24h,
    }


def count_open(session: Session) -> int:
    return int(session.execute(
        text("SELECT COUNT(*) FROM wrong_date_incidents WHERE status = 'open'")
    ).scalar() or 0)


_DATE_SETTING_KEYS = ("next_class_start", "next_open_house_date", "live_open_house_link")


def settings_changed_at(session: Session) -> datetime | None:
    """When the date/RSVP settings were last saved. Anything sent before this
    used older values, so it is stale by definition."""
    return session.execute(
        text("SELECT MAX(updated_at) FROM app_config WHERE key = ANY(:keys)"),
        {"keys": list(_DATE_SETTING_KEYS)},
    ).scalar()


def count_open_before(session: Session, cutoff: datetime) -> int:
    return int(session.execute(
        text("SELECT COUNT(*) FROM wrong_date_incidents "
             "WHERE status = 'open' AND message_sent_at < :c"),
        {"c": cutoff},
    ).scalar() or 0)


def dismiss_open_before(
    session: Session, cutoff: datetime, operator_id: str, note: str = "",
) -> int:
    """Dismiss every open incident whose message was sent before `cutoff`
    (no messages sent). Returns how many were dismissed. Idempotent: rerunning
    with the same cutoff dismisses nothing new."""
    now = datetime.now(tz=timezone.utc)
    rows = session.execute(
        text("""
            UPDATE wrong_date_incidents
            SET status = 'dismissed', resolved_by = :op, resolved_at = :now
            WHERE status = 'open' AND message_sent_at < :c
            RETURNING id
        """),
        {"op": operator_id, "now": now, "c": cutoff},
    ).fetchall()
    if rows:
        session.execute(
            text("""
                INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'wrong_date_incident', 'bulk', 'bulk_dismiss_wrong_date', :op,
                        CAST(:ctx AS jsonb), :now)
            """),
            {"id": str(uuid.uuid4()), "op": operator_id, "now": now,
             "ctx": json.dumps({"cutoff": cutoff.isoformat(), "count": len(rows), "note": note})},
        )
    _sync_aggregate_alert(session, now)
    return len(rows)


# ── Correction SMS ───────────────────────────────────────────────────────────

def build_correction_text(session: Session, settings: Any, channel: str = "email") -> str:
    """Correction text built from the CURRENT dashboard values (never the stale
    dates stored on the incident).

    email: full RSVP link allowed; no STOP line (GHL adds the unsubscribe footer).
    sms:   no long third-party links (carrier filtering) - only the free-signup
           address - and the opt-out line."""
    from app.core.schedule_context import DEFAULT_FREE_SIGNUP_URL, is_unset

    class_start, open_house = expected_dates(session, settings)
    sender = _config(session, settings, "sender_name", "Cora from Colaberry")
    rsvp = _config(session, settings, "live_open_house_link")
    stop = _config(session, settings, "unsubscribe_text")
    free_url = _config(session, settings, "free_signup_url", DEFAULT_FREE_SIGNUP_URL) or DEFAULT_FREE_SIGNUP_URL

    has_class = not is_unset(class_start)
    has_oh = not is_unset(open_house)
    facts = []
    if has_class:
        facts.append(f"our next class starts {class_start}")
    if has_oh:
        facts.append(f"our next Open House is {open_house}")
    if not has_class and not has_oh:
        facts.append("we don't have a class or Open House scheduled right now")
    elif not has_class:
        facts.append("there is no class scheduled yet")
    elif not has_oh:
        facts.append("there is no Open House scheduled right now")

    msg = f"Quick correction from {sender}: {' and '.join(facts)}."
    if channel == "sms":
        msg += f" Start learning for free at {free_url}."
        msg += " Sorry for any confusion!"
        if stop:
            msg += f" {stop}"
        return msg
    if has_oh and rsvp:
        msg += f" RSVP: {rsvp}."
    msg += f" You can also start learning for free at {free_url}."
    msg += " Sorry for any confusion!"
    return msg


DEFAULT_EMAIL_SUBJECT = "Correction: our class and Open House dates"
# GHL fields behind the "AI Agent - Send Email" workflow (after its Subject/Body were un-swapped
# 2026-10-01): Subject = Support Issue Ticket #2, Body = Message. The trigger is Ticket #2 changing.
EMAIL_SUBJECT_ATTR = "ghl_field_support_ticket_2"
EMAIL_BODY_ATTR = "ghl_field_message"


def correction_email_subject(session: Session, settings: Any) -> str:
    """Short subject for the correction email. Originates here (app_config
    `correction_email_subject`), never from whatever happens to be in a GHL field."""
    subject = " ".join(_config(session, settings, "correction_email_subject", DEFAULT_EMAIL_SUBJECT).split())
    return subject[:120] or DEFAULT_EMAIL_SUBJECT


def build_correction_email(session: Session, settings: Any) -> tuple[str, str]:
    """(subject, html_body) for the correction email, from the CURRENT Settings values."""
    from html import escape

    from app.core.schedule_context import DEFAULT_FREE_SIGNUP_URL, is_unset

    class_start, open_house = expected_dates(session, settings)
    sender = _config(session, settings, "sender_name", "Cora from Colaberry")
    rsvp = _config(session, settings, "live_open_house_link")
    free_url = _config(session, settings, "free_signup_url", DEFAULT_FREE_SIGNUP_URL) or DEFAULT_FREE_SIGNUP_URL
    free_href = free_url if free_url.startswith("http") else f"https://{free_url}"

    has_class = not is_unset(class_start)
    has_oh = not is_unset(open_house)
    items = []
    if has_class:
        items.append(f"<li>Next class starts: <strong>{escape(class_start)}</strong></li>")
    if has_oh:
        rsvp_html = f' &mdash; <a href="{escape(rsvp, quote=True)}">RSVP here</a>' if rsvp else ""
        items.append(f"<li>Next Open House: <strong>{escape(open_house)}</strong>{rsvp_html}</li>")
    if has_class or has_oh:
        facts = "<p>We recently sent you a message with an incorrect date. Here is the correct information:</p>"
        if items:
            facts += "<ul>" + "".join(items) + "</ul>"
        if not has_class:
            facts += "<p>There is no class scheduled yet.</p>"
        elif not has_oh:
            facts += "<p>There is no Open House scheduled right now.</p>"
    else:
        facts = ("<p>We recently sent you a message with a date that is no longer correct. "
                 "We don't have a class or Open House scheduled right now.</p>")
    html = (
        "<p>Hi there,</p>" + facts
        + f'<p>You can also start learning for free anytime at <a href="{escape(free_href, quote=True)}">'
          f"{escape(free_url)}</a>.</p>"
        + "<p>Sorry for any confusion!</p>"
        + f"<p>{escape(sender)}</p>"
    )
    return correction_email_subject(session, settings), html


def _channel_values(
    session: Session, settings: Any, channel: str, text_body: str, test_stamp: str | None = None,
) -> tuple[dict[str, str], str, str]:
    """(GHL field values keyed by Settings attribute, subject-for-record, body-for-record).

    email: Ticket #2 <- short subject, Message <- HTML body (both written in ONE update; the
           workflow's 5 s wait means Message is in place when it fires).
    sms:   Ticket #4 <- the text."""
    if channel == "email":
        subject, html = build_correction_email(session, settings)
        if test_stamp:
            subject = f"TEST [{test_stamp} UTC] {subject}"
            html = "<p><strong>TEST &mdash; please ignore.</strong></p>" + html
        return {EMAIL_SUBJECT_ATTR: subject, EMAIL_BODY_ATTR: html}, subject, html
    body = (f"TEST [{test_stamp}] - please ignore. " if test_stamp else "") + text_body
    return {channel_spec("sms").field_attr: body}, "", body


class IncidentNotOpen(Exception):
    """Incident missing or already corrected/dismissed."""


class ChannelDisabled(Exception):
    """SMS corrections are switched off (app_config sms_corrections_enabled)."""


# ── channels ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Channel:
    name: str
    field_attr: str        # Settings attribute holding the GHL field label the workflow reads
    ledger_col: str        # wrong_date_incidents column stamped when the send is triggered
    delay_key: str
    delay_default: float
    cap_key: str
    cap_default: int
    tcpa: bool             # apply the 08:00-21:00 local floor (texts only)


CHANNELS: dict[str, _Channel] = {
    # GHL "AI Agent - Send Email": trigger Support Issue Ticket #2 changed -> email body = Ticket #2
    "email": _Channel("email", "ghl_field_support_ticket_2", "email_triggered_at",
                      "correction_email_delay_seconds", 3.0, "correction_email_daily_cap", 100, False),
    # GHL "AI Agent - Send SMS": trigger Support issue Ticket #4 changed -> SMS body = Ticket #4
    "sms": _Channel("sms", "ghl_field_support_ticket_4", "sms_triggered_at",
                    "correction_send_delay_seconds", 5.0, "correction_daily_cap", 300, True),
}


def channel_spec(channel: str) -> _Channel:
    try:
        return CHANNELS[channel]
    except KeyError:
        raise ValueError(f"Unknown correction channel {channel!r}") from None


def sms_enabled(session: Session, settings: Any) -> bool:
    """SMS corrections are OFF unless app_config sms_corrections_enabled is true.
    (Test sends to allow-listed contacts work regardless - see send_test_correction.)"""
    return _config(session, settings, "sms_corrections_enabled", "false").strip().lower() in ("true", "1", "yes")


def _require_enabled(session: Session, settings: Any, channel: str) -> _Channel:
    spec = channel_spec(channel)
    if channel == "sms" and not sms_enabled(session, settings):
        raise ChannelDisabled("SMS corrections are disabled")
    return spec


def _send_delay(session: Session, settings: Any, channel: str = "email") -> float:
    """Seconds between REAL sends in a bulk run (provider rate-limit safety); 0 disables."""
    spec = channel_spec(channel)
    raw = _config(session, settings, spec.delay_key, str(spec.delay_default))
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return spec.delay_default


def _daily_cap(session: Session, settings: Any, channel: str = "email") -> int:
    """Max distinct leads corrected on this channel in any rolling 24h."""
    spec = channel_spec(channel)
    raw = _config(session, settings, spec.cap_key, str(spec.cap_default))
    try:
        return max(int(float(raw)), 0)
    except (TypeError, ValueError):
        return spec.cap_default


def sent_last_24h(session: Session, channel: str = "email") -> int:
    col = channel_spec(channel).ledger_col     # fixed internal constant, never user input
    return int(session.execute(
        text(f"SELECT COUNT(DISTINCT contact_id) FROM wrong_date_incidents "
             f"WHERE {col} >= now() - interval '24 hours'")
    ).scalar() or 0)


# ── eligibility screening (DND / STOP / windows) ─────────────────────────────

def _lead_row(session: Session, contact_id: str) -> dict | None:
    row = session.execute(
        text("""
            SELECT contact_id, campaign_name, do_not_call, invalid, status, last_replied_at, sales_outcome
            FROM lead_state WHERE contact_id = :c OR normalized_phone = :c LIMIT 1
        """),
        {"c": contact_id},
    ).fetchone()
    if row is None:
        return None
    keys = ("contact_id", "campaign_name", "do_not_call", "invalid", "status",
            "last_replied_at", "sales_outcome")
    return dict(zip(keys, row))


def _has_stop_reply(session: Session, contact_id: str) -> bool:
    rows = session.execute(
        text("SELECT body FROM inbound_messages WHERE contact_id = :c ORDER BY received_at DESC LIMIT 20"),
        {"c": contact_id},
    ).fetchall()
    return any(is_stop_reply(r[0]) for r in rows)


def _window_verdict(
    session: Session, settings: Any, contact_id: str, lead: dict | None, now: datetime,
    tcpa: bool = True,
) -> Verdict | None:
    """HOLD unless inside the lead's campaign window(s) - and, for texts, the TCPA
    hours - in the lead's own timezone. Lead with no known campaign must be inside
    every campaign window (the strictest reading)."""
    from zoneinfo import ZoneInfo

    from app.core.campaign_schedule import (
        get_contact_timezone,
        is_campaign_active,
        next_active_window_start,
    )

    campaigns = [lead["campaign_name"]] if lead and lead.get("campaign_name") else ["New Lead", "Cold Lead"]
    tz_name = get_contact_timezone(session, (lead or {}).get("contact_id") or contact_id, settings)
    local = now.astimezone(ZoneInfo(tz_name))
    if (not tcpa or in_tcpa_hours(local.hour)) and all(
        is_campaign_active(c, now, settings, tz_name, session) for c in campaigns
    ):
        return None
    opens = max(next_active_window_start(c, now, settings, tz_name, session) for c in campaigns)
    return Verdict(HOLD, "outside the allowed sending window", kind="window",
                   next_open=opens.isoformat())


def screen_lead(
    session: Session, settings: Any, contact_id: str, now: datetime, channel: str = "email",
) -> Verdict:
    """DB-only checks (no GHL call): Cora opt-out/state signals, then sending window.
    A STOP reply is an SMS opt-out, so it only blocks the SMS channel."""
    spec = channel_spec(channel)
    lead = _lead_row(session, contact_id)
    stop = channel == "sms" and _has_stop_reply(session, contact_id)
    v = cora_state_verdict(lead, stop)
    if v is not None:
        return v
    return _window_verdict(session, settings, contact_id, lead, now, tcpa=spec.tcpa) or Verdict(SEND)


def _looks_like_phone(s: str) -> bool:
    stripped = s.replace(" ", "").replace("-", "").replace("+", "")
    return bool(stripped) and stripped.isdigit()


def _lookup_contact(ghl: Any, contact_id: str) -> tuple[str, dict]:
    """(real GHL contact id, full contact record incl. DND flags). Raises on failure,
    so a lead whose DND state can't be read is never contacted (fail closed)."""
    real = contact_id
    if _looks_like_phone(contact_id):
        found = ghl.search_contact_by_phone(contact_id)
        if not found or not found.get("id"):
            raise RuntimeError(f"Could not resolve GHL contact for phone {contact_id}")
        real = found["id"]
    rec = ghl.get_contact(real)
    if isinstance(rec, dict):
        rec = rec.get("contact", rec)
    return real, rec if isinstance(rec, dict) else {}


def _block_lead(session: Session, contact_id: str, reason: str, now: datetime) -> int:
    """Close this lead's open incidents as dismissed with the reason. Never retried."""
    ids = [r[0] for r in session.execute(
        text("""
            UPDATE wrong_date_incidents
            SET status = 'dismissed', resolved_by = :by, resolved_at = :now
            WHERE contact_id = :c AND status = 'open'
            RETURNING id
        """),
        {"by": ("auto-skip: " + reason)[:240], "now": now, "c": contact_id},
    ).fetchall()]
    if ids:
        session.execute(
            text("""
                INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
                VALUES (:id, 'wrong_date_incident', :eid, 'auto_skip_date_correction', 'system',
                        CAST(:ctx AS jsonb), :now)
            """),
            {"id": str(uuid.uuid4()), "eid": ids[0], "now": now,
             "ctx": json.dumps({"reason": reason, "incidents": len(ids)})},
        )
        _sync_aggregate_alert(session, now)
    return len(ids)


def _skipped_result(verdict: Verdict, correction: str, channel: str) -> dict[str, Any]:
    return {
        "shadow": False, "sent": False, "skipped": True, "correction_text": correction,
        "channel": channel, "held": verdict.action == HOLD, "reason": verdict.reason,
        "next_open": verdict.next_open,
    }


def _push_to_ghl(
    settings: Any, ghl: Any, flags: Any, real_contact_id: str, values: dict[str, str],
    field_cache: dict | None = None,
) -> None:
    """Write `values` ({Settings attribute of a GHL field label: text}) in ONE contact update.

    email: Ticket #2 <- subject (its change starts workflow "AI Agent - Send Email"), Message <- body.
    sms:   Ticket #4 <- text (its change starts workflow "AI Agent - Send SMS").
    New text differs from the lead's previous value, which is what makes "has changed" fire;
    byte-identical text sent twice changes nothing, so GHL cannot double-send it either."""
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids

    cache = field_cache if field_cache is not None else {}
    updates: dict[str, str] = {}
    for attr, value in values.items():
        label = getattr(settings, attr, None)
        if not label:
            raise RuntimeError(
                f"{attr.upper()} is not configured - a correction cannot be sent without it"
            )
        if attr not in cache:
            resolved = _resolve_to_field_ids(ghl, {label: "x"})
            if not resolved:
                raise RuntimeError(f"Could not resolve GHL field {label!r} to an ID")
            cache[attr] = next(iter(resolved))
        updates[cache[attr]] = value
    ghl.update_contact_fields(real_contact_id, updates, mode_flags=flags)


def send_correction(
    session: Session, settings: Any, incident_id: str, operator_id: str,
    channel: str = "email", field_cache: dict | None = None, before_write: Any = None,
) -> dict[str, Any]:
    """
    Send the correction for one open incident on `channel` (default email).

    Order of safeguards (nothing is written until all pass):
      0. SMS only: switched off unless app_config sms_corrections_enabled (ChannelDisabled)
      1. shadow / GHL-writes-off      -> {"shadow": True}, incident stays open
      2. Cora signals (do-not-contact, invalid, closed, not-interested; STOP for SMS)
                                      -> BLOCK: incidents dismissed with the reason
      3. lead replied / outside the campaign window (+ TCPA hours for SMS)
                                      -> HOLD: incident stays open, {"held": True}
      4. GHL contact: DND (all / this channel), opt-out tag, no email/phone on file
                                      -> BLOCK (a contact we can't read is NOT contacted)
      5. atomic claim (open -> corrected) so a double-click can't double-send;
         rolled back if the GHL write fails.
    `before_write` (optional callable) runs after every check passed and just before
    the claim - bulk runs use it to pace real sends only, never skipped leads.
    """
    from app.adapters.ghl import GHLClient
    from app.core.mode_flags import get_mode_flags

    spec = _require_enabled(session, settings, channel)
    row = session.execute(
        text("SELECT contact_id, status FROM wrong_date_incidents WHERE id = :id"),
        {"id": incident_id},
    ).fetchone()
    if row is None or row[1] != "open":
        raise IncidentNotOpen(incident_id)
    contact_id = row[0]

    correction = build_correction_text(session, settings, channel)
    values, rec_subject, rec_body = _channel_values(session, settings, channel, correction)
    flags = get_mode_flags(session, settings)
    if not flags.ghl_writes_enabled:
        return {"shadow": True, "correction_text": correction, "sent": False, "channel": channel}

    now = datetime.now(tz=timezone.utc)
    verdict = screen_lead(session, settings, contact_id, now, channel)
    if verdict.action == BLOCK:
        _block_lead(session, contact_id, verdict.reason, now)
        return _skipped_result(verdict, correction, channel)
    if verdict.action == HOLD:
        return _skipped_result(verdict, correction, channel)

    ghl = GHLClient(settings=settings)
    real_id, record = _lookup_contact(ghl, contact_id)
    reason = contact_block_reason(record, channel)
    if reason:
        _block_lead(session, contact_id, reason, now)
        return _skipped_result(Verdict(BLOCK, reason), correction, channel)

    if before_write is not None:
        before_write()

    col = spec.ledger_col   # fixed internal constant, never user input
    claimed = session.execute(
        text(f"""
            UPDATE wrong_date_incidents
            SET status = 'corrected', resolved_by = :op, resolved_at = :now, correction_text = :txt,
                correction_channel = :ch, {col} = :now
            WHERE id = :id AND status = 'open'
            RETURNING id
        """),
        {"op": operator_id, "now": now, "txt": correction, "ch": channel, "id": incident_id},
    ).fetchone()
    if claimed is None:
        raise IncidentNotOpen(incident_id)
    session.flush()

    try:
        _push_to_ghl(settings, ghl, flags, real_id, values, field_cache)
    except Exception:
        session.execute(
            text(f"""
                UPDATE wrong_date_incidents
                SET status = 'open', resolved_by = NULL, resolved_at = NULL, correction_text = NULL,
                    correction_channel = NULL, {col} = NULL
                WHERE id = :id
            """),
            {"id": incident_id},
        )
        session.flush()
        raise

    # History: the correction is a real outbound message (its dates are correct,
    # so the scanner will not flag it).
    session.execute(
        text("""
            INSERT INTO outbound_messages (id, contact_id, channel, subject, body, status, created_at)
            VALUES (:id, :cid, :ch, :subj, :body, 'sent', :now)
        """),
        {"id": str(uuid.uuid4()), "cid": row[0], "ch": channel, "subj": rec_subject or None,
         "body": rec_body, "now": now},
    )
    session.execute(
        text("""
            INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES (:id, 'wrong_date_incident', :eid, 'send_date_correction', :op,
                    CAST(:ctx AS jsonb), :now)
        """),
        {"id": str(uuid.uuid4()), "eid": incident_id, "op": operator_id,
         "ctx": json.dumps({"source": "dashboard_v2", "channel": channel}), "now": now},
    )
    # One correction covers everything this lead was told wrong: close their other
    # open incidents too so the lead is never sent the same correction twice.
    siblings = session.execute(
        text(f"""
            UPDATE wrong_date_incidents
            SET status = 'corrected', resolved_by = :op, resolved_at = :now, correction_text = :txt,
                correction_channel = :ch, {col} = :now
            WHERE contact_id = :cid AND status = 'open'
            RETURNING id
        """),
        {"op": operator_id, "now": now, "txt": correction, "ch": channel, "cid": row[0]},
    ).fetchall()
    _sync_aggregate_alert(session, now)
    return {
        "shadow": False, "correction_text": correction, "sent": True, "skipped": False,
        "channel": channel, "also_closed": len(siblings),
    }


@dataclass
class _RunTally:
    sent: int = 0
    failed: int = 0
    blocked: int = 0
    held: int = 0
    next_open: str | None = None
    errors: list[str] = field(default_factory=list)

    def note_hold(self, next_open: str | None) -> None:
        self.held += 1
        if next_open and (self.next_open is None or next_open < self.next_open):
            self.next_open = next_open


def _result(tally: _RunTally, total: int, remaining: int, stopped_early: bool,
            cap: int, cap_hit: bool, session: Session, channel: str) -> dict[str, Any]:
    return {
        "shadow": False, "channel": channel, "sent": tally.sent, "failed": tally.failed,
        "total_leads": total, "remaining": remaining, "errors": tally.errors[:5],
        "stopped_early": stopped_early, "blocked": tally.blocked, "held": tally.held,
        "next_window_opens": tally.next_open, "daily_cap": cap, "daily_cap_reached": cap_hit,
        "sent_last_24h": sent_last_24h(session, channel),
    }


def dismiss_incident(session: Session, incident_id: str, operator_id: str, note: str = "") -> None:
    """Mark an incident as a false positive / handled elsewhere. No message sent."""
    now = datetime.now(tz=timezone.utc)
    done = session.execute(
        text("""
            UPDATE wrong_date_incidents
            SET status = 'dismissed', resolved_by = :op, resolved_at = :now
            WHERE id = :id AND status = 'open' RETURNING id
        """),
        {"op": operator_id, "now": now, "id": incident_id},
    ).fetchone()
    if done is None:
        raise IncidentNotOpen(incident_id)
    session.execute(
        text("""
            INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES (:id, 'wrong_date_incident', :eid, 'dismiss_wrong_date', :op,
                    CAST(:ctx AS jsonb), :now)
        """),
        {"id": str(uuid.uuid4()), "eid": incident_id, "op": operator_id,
         "ctx": json.dumps({"note": note}), "now": now},
    )
    _sync_aggregate_alert(session, now)


# One bulk HTTP request handles at most this many SENDS (each ~1s + the pacing
# delay) so it stays well inside proxy timeouts. Skipped/held leads don't count.
# The dashboard button keeps calling until `remaining` is 0.
BULK_SEND_MAX_LEADS = 10
BULK_SEND_MAX_CONSECUTIVE_FAILURES = 3
# Wall-clock budget for ONE bulk request. The dashboard reaches this API through the
# Next.js server's rewrite proxy, which drops any request open longer than ~30 s
# (browser sees "Internal Server Error" while the API keeps working - seen live
# 2026-10-01 with 10 sends x ~4 s). Stay well under it; the tile loops until done.
BULK_TIME_BUDGET_SECONDS = 18.0


def count_open_leads(session: Session) -> int:
    """Distinct leads with at least one open incident (= corrections a bulk send would send)."""
    return int(session.execute(
        text("SELECT COUNT(DISTINCT contact_id) FROM wrong_date_incidents WHERE status = 'open'")
    ).scalar() or 0)


def send_corrections_bulk(
    session: Session, settings: Any, operator_id: str, channel: str = "email",
) -> dict[str, Any]:
    """
    Send the correction to every lead with an open incident on `channel` (default
    email) - ONE message per lead, however many wrong messages they received. Each
    lead goes through every safeguard in send_correction. Pacing: the channel's
    delay setting between REAL sends only. Daily cap: the channel's cap, distinct
    leads per rolling 24h. Each lead commits independently; 3 consecutive failures
    stop the run. Shadow mode sends nothing.
    """
    from app.core.mode_flags import get_mode_flags

    _require_enabled(session, settings, channel)
    leads = session.execute(
        text("""
            SELECT DISTINCT ON (contact_id) contact_id, id
            FROM wrong_date_incidents
            WHERE status = 'open'
            ORDER BY contact_id, created_at DESC
        """)
    ).fetchall()
    total = len(leads)

    flags = get_mode_flags(session, settings)
    if not flags.ghl_writes_enabled:
        return {"shadow": True, "channel": channel, "sent": 0, "failed": 0, "remaining": total,
                "total_leads": total, "correction_text": build_correction_text(session, settings, channel)}

    tally = _RunTally()
    cap = _daily_cap(session, settings, channel)
    used = sent_last_24h(session, channel)
    delay = _send_delay(session, settings, channel)
    field_cache: dict = {}
    consecutive = attempts = 0
    did_send = cap_hit = False
    remaining = 0
    started = _monotonic()

    def pace() -> None:
        # space real sends out - provider rate-limit safety. Skipped/held leads never wait.
        if did_send and delay:
            _sleep(delay)

    for idx, (contact_id, incident_id) in enumerate(leads):
        if attempts >= BULK_SEND_MAX_LEADS:
            remaining = total - idx
            break
        if idx > 0 and _monotonic() - started >= BULK_TIME_BUDGET_SECONDS:
            remaining = total - idx      # out of time for this request - the tile calls again
            break
        if used + tally.sent >= cap:
            cap_hit, remaining = True, total - idx
            break
        try:
            r = send_correction(session, settings, incident_id, operator_id, channel=channel,
                                field_cache=field_cache, before_write=pace)
            session.commit()
        except IncidentNotOpen:
            session.rollback()  # closed by a concurrent click - nothing to do
            continue
        except Exception as exc:
            session.rollback()
            attempts += 1
            did_send = True
            tally.failed += 1
            consecutive += 1
            tally.errors.append(f"{contact_id}: {exc}")
            logger.error("bulk correction failed | channel=%s contact=%s: %s", channel, contact_id, exc)
            if consecutive >= BULK_SEND_MAX_CONSECUTIVE_FAILURES:
                return _result(tally, total, max(total - idx - 1, 0), True, cap, cap_hit, session, channel)
            continue
        if r.get("skipped"):
            if r.get("held"):
                tally.note_hold(r.get("next_open"))
            else:
                tally.blocked += 1
            continue
        attempts += 1
        did_send = True
        tally.sent += 1
        consecutive = 0
    return _result(tally, total, remaining, False, cap, cap_hit, session, channel)


def send_test_correction(
    session: Session, settings: Any, channel: str, operator_id: str, email: str | None = None,
) -> dict[str, Any]:
    """
    Send a clearly-labelled TEST correction to an allow-listed contact (the operator's
    own GHL contact) to prove a channel's workflow still works - e.g. SMS while real
    SMS corrections are switched off. Allow-list: app_config `correction_test_contacts`
    (comma-separated emails; empty = feature unavailable). Safeguards: the target must
    be on the allow-list AND match exactly one GHL contact by exact email; DND / missing
    address are respected; no incident or counter is touched; audited.
    """
    from app.adapters.ghl import GHLClient
    from app.core.mode_flags import get_mode_flags

    spec = channel_spec(channel)
    allow = [e.strip().lower() for e in _config(session, settings, "correction_test_contacts", "").split(",") if e.strip()]
    if not allow:
        raise ValueError("No test contacts configured (app_config correction_test_contacts)")
    target = (email or allow[0]).strip().lower()
    if target not in allow:
        raise ValueError("That contact is not on the test allow-list")

    flags = get_mode_flags(session, settings)
    if not flags.ghl_writes_enabled:
        return {"shadow": True, "sent": False, "channel": channel}

    ghl = GHLClient(settings=settings)
    matches = [c for c in ghl.search_contacts_by_query(target) if (c.get("email") or "").strip().lower() == target]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one GHL contact for the test address, found {len(matches)}")
    real_id, record = _lookup_contact(ghl, matches[0]["id"])
    reason = contact_block_reason(record, channel)
    if reason:
        return {"sent": False, "skipped": True, "reason": reason, "channel": channel}

    now = datetime.now(tz=timezone.utc)
    stamp = now.strftime("%H:%M:%S")
    values, subject, body = _channel_values(
        session, settings, channel, build_correction_text(session, settings, channel), test_stamp=stamp)
    _push_to_ghl(settings, ghl, flags, real_id, values, None)
    session.execute(
        text("""
            INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES (:id, 'wrong_date_test', :eid, 'send_test_correction', :op, CAST(:ctx AS jsonb), :now)
        """),
        {"id": str(uuid.uuid4()), "eid": real_id, "op": operator_id, "now": now,
         "ctx": json.dumps({"channel": channel, "field": spec.field_attr})},
    )
    return {"sent": True, "skipped": False, "channel": channel, "correction_text": body, "subject": subject}
