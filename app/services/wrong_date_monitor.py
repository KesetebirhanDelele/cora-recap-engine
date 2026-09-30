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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

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
_KIND_LABEL = {KIND_CLASS: "class start", "open_house": "open house"}


def _config(session: Session, settings: Any, key: str, default: str = "") -> str:
    from app.core.app_config import get_str

    return get_str(key, session, settings, default)


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
            expected_class_start=class_start, expected_open_house=open_house,
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
            f"{_KIND_LABEL[w.kind]} date '{w.raw}' (should be {w.expected})" for w in wrong
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

def build_correction_text(session: Session, settings: Any) -> str:
    """Correction SMS built from the CURRENT dashboard values (never the
    stale dates stored on the incident)."""
    class_start, open_house = expected_dates(session, settings)
    sender = _config(session, settings, "sender_name", "Cora from Colaberry")
    rsvp = _config(session, settings, "live_open_house_link")
    stop = _config(session, settings, "unsubscribe_text")

    facts = []
    if class_start:
        facts.append(f"our next class starts {class_start}")
    if open_house:
        facts.append(f"our next Open House is {open_house}")
    if not facts:
        raise ValueError("Neither next_class_start nor next_open_house_date is set")

    msg = f"Quick correction from {sender}: {' and '.join(facts)}."
    if rsvp and open_house:
        msg += f" RSVP: {rsvp}."
    msg += " Sorry for any confusion!"
    if stop:
        msg += f" {stop}"
    return msg


class IncidentNotOpen(Exception):
    """Incident missing or already corrected/dismissed."""


def _looks_like_phone(s: str) -> bool:
    stripped = s.replace(" ", "").replace("-", "").replace("+", "")
    return bool(stripped) and stripped.isdigit()


def send_correction(
    session: Session, settings: Any, incident_id: str, operator_id: str,
    field_cache: dict | None = None,
) -> dict[str, Any]:
    """
    Send the correction SMS for one open incident by writing the GHL "Message"
    field (a GHL workflow sends it, same as VM follow-ups).

    - Shadow / GHL-writes-off: nothing is written and the incident STAYS open;
      returns {"shadow": True, ...}.
    - Live: the incident is claimed atomically (open -> corrected) BEFORE the
      write so a double-click or second operator can't send twice; if the GHL
      write fails the claim is rolled back and the exception propagates.
    """
    from app.adapters.ghl import GHLClient
    from app.core.mode_flags import get_mode_flags
    from app.worker.jobs.crm_jobs import _resolve_to_field_ids

    row = session.execute(
        text("SELECT contact_id, status FROM wrong_date_incidents WHERE id = :id"),
        {"id": incident_id},
    ).fetchone()
    if row is None or row[1] != "open":
        raise IncidentNotOpen(incident_id)
    contact_id = row[0]

    correction = build_correction_text(session, settings)
    flags = get_mode_flags(session, settings)
    if not flags.ghl_writes_enabled:
        return {"shadow": True, "correction_text": correction, "sent": False}

    label = getattr(settings, "ghl_field_message", None)
    if not label:
        raise RuntimeError("GHL_FIELD_MESSAGE is not configured — cannot send correction")

    now = datetime.now(tz=timezone.utc)
    claimed = session.execute(
        text("""
            UPDATE wrong_date_incidents
            SET status = 'corrected', resolved_by = :op, resolved_at = :now, correction_text = :txt
            WHERE id = :id AND status = 'open'
            RETURNING id
        """),
        {"op": operator_id, "now": now, "txt": correction, "id": incident_id},
    ).fetchone()
    if claimed is None:
        raise IncidentNotOpen(incident_id)
    session.flush()

    try:
        ghl = GHLClient(settings=settings)
        if _looks_like_phone(contact_id):
            found = ghl.search_contact_by_phone(contact_id)
            if not found or not found.get("id"):
                raise RuntimeError(f"Could not resolve GHL contact for phone {contact_id}")
            contact_id = found["id"]
        if field_cache is not None and "field_id" in field_cache:
            field_updates = {field_cache["field_id"]: correction}
        else:
            field_updates = _resolve_to_field_ids(ghl, {label: correction})
            if not field_updates:
                raise RuntimeError(f"Could not resolve GHL field {label!r} to an ID")
            if field_cache is not None:
                field_cache["field_id"] = next(iter(field_updates))
        ghl.update_contact_fields(contact_id, field_updates, mode_flags=flags)
    except Exception:
        session.execute(
            text("""
                UPDATE wrong_date_incidents
                SET status = 'open', resolved_by = NULL, resolved_at = NULL, correction_text = NULL
                WHERE id = :id
            """),
            {"id": incident_id},
        )
        session.flush()
        raise

    # History: the correction is a real outbound SMS (its dates are correct,
    # so the scanner will not flag it).
    session.execute(
        text("""
            INSERT INTO outbound_messages (id, contact_id, channel, body, status, created_at)
            VALUES (:id, :cid, 'sms', :body, 'sent', :now)
        """),
        {"id": str(uuid.uuid4()), "cid": row[0], "body": correction, "now": now},
    )
    session.execute(
        text("""
            INSERT INTO audit_log (id, entity_type, entity_id, action, operator_id, context_json, created_at)
            VALUES (:id, 'wrong_date_incident', :eid, 'send_date_correction', :op,
                    CAST(:ctx AS jsonb), :now)
        """),
        {"id": str(uuid.uuid4()), "eid": incident_id, "op": operator_id,
         "ctx": '{"source": "dashboard_v2"}', "now": now},
    )
    # One correction covers everything this lead was told wrong: close their
    # other open incidents too so nobody sends (and the lead never receives)
    # the same correction twice.
    siblings = session.execute(
        text("""
            UPDATE wrong_date_incidents
            SET status = 'corrected', resolved_by = :op, resolved_at = :now, correction_text = :txt
            WHERE contact_id = :cid AND status = 'open'
            RETURNING id
        """),
        {"op": operator_id, "now": now, "txt": correction, "cid": row[0]},
    ).fetchall()
    _sync_aggregate_alert(session, now)
    return {
        "shadow": False, "correction_text": correction, "sent": True,
        "also_closed": len(siblings),
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


# One bulk HTTP request handles at most this many leads (~1s each) so it stays
# well inside proxy timeouts. The dashboard button keeps calling until
# `remaining` is 0, so the operator still sends to everyone with one click.
BULK_SEND_MAX_LEADS = 25
BULK_SEND_MAX_CONSECUTIVE_FAILURES = 3


def count_open_leads(session: Session) -> int:
    """Distinct leads with at least one open incident (= SMS a bulk send would send)."""
    return int(session.execute(
        text("SELECT COUNT(DISTINCT contact_id) FROM wrong_date_incidents WHERE status = 'open'")
    ).scalar() or 0)


def send_corrections_bulk(
    session: Session, settings: Any, operator_id: str,
) -> dict[str, Any]:
    """
    Send the correction SMS to every lead with an open incident - ONE SMS per
    lead, however many wrong messages they received (send_correction closes
    the lead's sibling incidents). Each lead commits independently, so a
    failure part-way keeps everything already sent; after
    BULK_SEND_MAX_CONSECUTIVE_FAILURES in a row the run stops (GHL is down or
    misconfigured - don't hammer it). In shadow mode nothing is written.
    """
    from app.core.mode_flags import get_mode_flags

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
        return {"shadow": True, "sent": 0, "failed": 0, "remaining": total, "total_leads": total,
                "correction_text": build_correction_text(session, settings)}

    sent = failed = consecutive = 0
    errors: list[str] = []
    field_cache: dict = {}
    attempted = 0
    for contact_id, incident_id in leads[:BULK_SEND_MAX_LEADS]:
        attempted += 1
        try:
            send_correction(session, settings, incident_id, operator_id, field_cache=field_cache)
            session.commit()
            sent += 1
            consecutive = 0
        except IncidentNotOpen:
            session.rollback()  # closed by a concurrent click - nothing to do
        except Exception as exc:
            session.rollback()
            failed += 1
            consecutive += 1
            errors.append(f"{contact_id}: {exc}")
            logger.error("bulk correction failed | contact=%s: %s", contact_id, exc)
            if consecutive >= BULK_SEND_MAX_CONSECUTIVE_FAILURES:
                break
    return {
        "shadow": False, "sent": sent, "failed": failed, "total_leads": total,
        "remaining": max(total - attempted, 0),
        "errors": errors[:5],
        "stopped_early": consecutive >= BULK_SEND_MAX_CONSECUTIVE_FAILURES,
    }
