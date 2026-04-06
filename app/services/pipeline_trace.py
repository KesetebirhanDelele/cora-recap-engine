"""
Pipeline trace service — builds a full per-lead lifecycle trace from Postgres.

Aggregates all scheduled_jobs, shadow_actions, exceptions, and call_events
for a single contact_id into an ordered timeline of pipeline steps.

Used by GET /dashboard/lead/{contact_id}/trace.

Design rules:
  - No joins across more than 3 tables.
  - All queries use parameterized SQL via text().
  - Shadow payloads are included when is_shadow=True (SMS/email message body,
    GHL field write preview).
  - Returns None if the lead cannot be found by contact_id or phone.
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Fields to include in payload_summary (safe subset — no transcript / credentials)
_PAYLOAD_SUMMARY_KEYS = (
    "call_id",
    "campaign_name",
    "call_status",
    "duration_seconds",
    "attempt_number",
    "vm_tier",
    "intent_reason",
    "channel",
    "job_type",
)


def get_lead_trace(
    session: Session,
    contact_id: str,
    phone: str | None = None,
) -> dict[str, Any] | None:
    """
    Build a full pipeline trace for a lead.

    Returns None if no lead_state row is found for contact_id.
    Falls back to phone lookup if contact_id not found and phone is provided.
    """
    # ── 1. Resolve lead_state ─────────────────────────────────────────────────
    lead_row = session.execute(text("""
        SELECT contact_id, normalized_phone, campaign_name, status, ai_campaign_value
        FROM lead_state
        WHERE contact_id = :contact_id
        LIMIT 1
    """), {"contact_id": contact_id}).fetchone()

    # Phone fallback: use explicit ?phone= param, or auto-detect when contact_id
    # looks like a phone number (starts with + or is all digits).
    phone_lookup = phone or (
        contact_id if (contact_id.startswith("+") or contact_id.isdigit()) else None
    )
    if lead_row is None and phone_lookup:
        lead_row = session.execute(text("""
            SELECT contact_id, normalized_phone, campaign_name, status, ai_campaign_value
            FROM lead_state
            WHERE normalized_phone = :phone
            ORDER BY created_at DESC
            LIMIT 1
        """), {"phone": phone_lookup}).fetchone()

    if lead_row is None:
        return None

    resolved_contact_id = lead_row[0]
    normalized_phone = lead_row[1]
    campaign_name = lead_row[2]
    lead_status = lead_row[3]
    ai_campaign_value = lead_row[4]

    # ── 2. Fetch scheduled_jobs ───────────────────────────────────────────────
    job_rows = session.execute(text("""
        SELECT
            id,
            job_type,
            status,
            run_at,
            claimed_at,
            updated_at,
            payload_json
        FROM scheduled_jobs
        WHERE entity_type = 'lead' AND entity_id = :contact_id
        ORDER BY run_at ASC, created_at ASC
    """), {"contact_id": resolved_contact_id}).fetchall()

    # ── 3. Fetch shadow_actions for this lead ─────────────────────────────────
    shadow_rows = session.execute(text("""
        SELECT action_type, payload, created_at
        FROM shadow_actions
        WHERE contact_id = :contact_id
        ORDER BY created_at ASC
    """), {"contact_id": resolved_contact_id}).fetchall()

    # Build a lookup: job_type → list of shadow payloads (ordered by time)
    shadow_by_type: dict[str, list[dict]] = {}
    for srow in shadow_rows:
        atype = srow[0]
        raw_payload = srow[1] or {}
        if isinstance(raw_payload, str):
            import json as _json
            try:
                raw_payload = _json.loads(raw_payload)
            except (ValueError, TypeError):
                raw_payload = {}
        shadow_by_type.setdefault(atype, []).append(raw_payload)

    # Track which shadow rows have been consumed (by index per type)
    shadow_consumed: dict[str, int] = {}

    # ── 4. Fetch exceptions linked to this contact via context_json ──────────
    # Exceptions store contact_id in context_json (set by create_exception callers).
    # SQLite uses JSON_EXTRACT; Postgres uses ->>.
    # We fetch all open/resolved exceptions and match them in Python by entity_id
    # (call_id for call exceptions) or context_json contact_id.
    # Fetch all exceptions for this lead and filter in Python
    # (avoids JSON path syntax differences between SQLite and Postgres)
    exc_rows = session.execute(text("""
        SELECT id, entity_type, entity_id, type, resolution_reason, context_json
        FROM exceptions
        WHERE entity_type = 'lead' AND entity_id = :contact_id
        ORDER BY created_at ASC
    """), {"contact_id": resolved_contact_id}).fetchall()

    # Build lookup: entity_id (e.g. call_id) → exception info
    exc_by_entity_id: dict[str, dict] = {}
    for r in exc_rows:
        eid = r[2]
        if eid:
            exc_by_entity_id[eid] = {
                "exception_id": r[0],
                "failure_reason": r[4] or r[3],  # resolution_reason or type
            }

    # ── 5. Build steps ────────────────────────────────────────────────────────
    steps = []
    for row in job_rows:
        job_id = row[0]
        job_type = row[1]
        status = row[2]
        run_at = row[3]
        claimed_at = row[4]
        updated_at = row[5]
        payload_json = row[6] or {}
        if isinstance(payload_json, str):
            import json as _json
            try:
                payload_json = _json.loads(payload_json)
            except (ValueError, TypeError):
                payload_json = {}

        started_at = claimed_at or run_at
        # completed_at / failed_at not stored separately; use updated_at for terminal states
        ended_at = updated_at if status in ("completed", "failed", "cancelled") else None

        duration_ms: int | None = None
        if started_at and ended_at:
            started_utc = _to_datetime(started_at)
            ended_utc = _to_datetime(ended_at)
            if started_utc and ended_utc:
                duration_ms = int((ended_utc - started_utc).total_seconds() * 1000)

        # Determine shadow status and payload
        is_shadow, shadow_payload = _get_shadow_info(
            job_type=job_type,
            payload_json=payload_json,
            shadow_by_type=shadow_by_type,
            shadow_consumed=shadow_consumed,
        )

        # Match exceptions by job entity_id or job_id itself
        exc_info = exc_by_entity_id.get(job_id, exc_by_entity_id.get(
            (payload_json or {}).get("call_id", ""), {}
        ))
        exception_id = exc_info.get("exception_id")
        failure_reason = exc_info.get("failure_reason")

        payload_summary = _safe_payload_summary(payload_json)

        steps.append({
            "job_id": job_id,
            "job_type": job_type,
            "started_at": _iso(started_at),
            "completed_at": _iso(ended_at),
            "duration_ms": duration_ms,
            "status": status,
            "is_shadow": is_shadow,
            "shadow_payload": shadow_payload,
            "exception_id": exception_id,
            "failure_reason": failure_reason,
            "payload_summary": payload_summary,
        })

    return {
        "contact_id": resolved_contact_id,
        "normalized_phone": normalized_phone,
        "campaign_name": campaign_name,
        "status": lead_status,
        "ai_campaign_value": ai_campaign_value,
        "steps": steps,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_shadow_info(
    job_type: str,
    payload_json: dict,
    shadow_by_type: dict[str, list[dict]],
    shadow_consumed: dict[str, int],
) -> tuple[bool, dict | None]:
    """
    Determine if a job step was executed in shadow mode and return its payload.

    Shadow actions are matched to jobs by channel type (sms/email)
    or action_type (ghl_contact_update).
    """
    # SMS job → match shadow action with action_type='sms'
    if job_type in ("send_sms", "send_sms_followup"):
        return _consume_shadow(shadow_by_type, shadow_consumed, "sms")

    # Email job → match shadow action with action_type='email'
    if job_type in ("send_email", "send_email_followup"):
        return _consume_shadow(shadow_by_type, shadow_consumed, "email")

    # GHL update job → match shadow action with action_type='ghl_contact_update'
    if job_type in ("update_ghl_after_vm_message", "finalize_lead"):
        shadow_list = shadow_by_type.get("ghl_contact_update", [])
        idx = shadow_consumed.get("ghl_contact_update", 0)
        if idx < len(shadow_list):
            shadow_consumed["ghl_contact_update"] = idx + 1
            shadow_data = shadow_list[idx]
            return True, {"fields": shadow_data.get("fields", shadow_data)}

    # outbound_call job executed in shadow → no real Synthflow call
    if job_type in ("launch_outbound_call",):
        shadow_list = shadow_by_type.get("outbound_call", [])
        idx = shadow_consumed.get("outbound_call", 0)
        if idx < len(shadow_list):
            shadow_consumed["outbound_call"] = idx + 1
            return True, shadow_list[idx]

    return False, None


def _consume_shadow(
    shadow_by_type: dict[str, list[dict]],
    shadow_consumed: dict[str, int],
    action_type: str,
) -> tuple[bool, dict | None]:
    """Pop the next unconsumed shadow action of the given type."""
    shadow_list = shadow_by_type.get(action_type, [])
    idx = shadow_consumed.get(action_type, 0)
    if idx < len(shadow_list):
        shadow_consumed[action_type] = idx + 1
        raw = shadow_list[idx]
        payload: dict = {}
        if "message_body" in raw:
            payload["message_body"] = raw["message_body"]
        if "email_subject" in raw:
            payload["email_subject"] = raw["email_subject"]
        if not payload:
            payload = raw
        return True, payload
    return False, None


def _safe_payload_summary(payload_json: dict) -> dict:
    """Extract a safe subset of job payload fields (no PII beyond contact_id)."""
    return {k: payload_json[k] for k in _PAYLOAD_SUMMARY_KEYS if k in payload_json}


def _to_datetime(dt: Any) -> "datetime | None":
    """Convert a value to a timezone-aware datetime (handles strings from SQLite)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            from datetime import datetime as _dt
            parsed = _dt.fromisoformat(dt.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    if hasattr(dt, "tzinfo"):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _iso(dt: Any) -> str | None:
    """Return ISO 8601 string for a datetime, or None. Handles both datetime objects and strings."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt  # already a string (SQLite returns strings for datetime columns)
    if hasattr(dt, "tzinfo") and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
