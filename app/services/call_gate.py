"""
Opt-out call gate (spec/39): read the lead's live GHL record before every outbound call.

  allow  - nothing in GHL says stop, or GHL has no such contact (nothing to read, same as before)
  block  - GHL do-not-disturb / Call DND / an opt-out tag: the call job is cancelled, the lead's other pending jobs are cancelled,
           and (when the person asked us to stop) Cora's own do_not_call flag is set so nothing else re-enters them
  defer  - GHL could not be read (outage, rate limit): fail CLOSED - the job is retried in 5 minutes; after 6 failed attempts it is
           cancelled and a warning exception is raised. A lead whose opt-out state cannot be read is never called.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core import call_gate as cg

logger = logging.getLogger(__name__)

MAX_LOOKUP_ATTEMPTS = 6
RETRY_SECONDS = 300


@dataclass(frozen=True)
class GateResult:
    action: str                    # allow | block | defer | give_up
    reason: str = ""
    strong: bool = False


def _cfg(session: Session, settings: Any, key: str, default: str) -> str:
    from app.core.app_config import get_config_value

    v = get_config_value(key, session, settings, default)
    return default if v is None else str(v)


def enabled(session: Session, settings: Any) -> bool:
    return _cfg(session, settings, "call_gate_enabled", "true").strip().lower() not in ("false", "0", "no", "off")


def check(session: Session, settings: Any, contact_id: str, *, job_id: str, ghl: Any = None) -> GateResult:
    """Look the lead up in GHL and decide. Never raises."""
    if not contact_id:
        return GateResult("allow")
    try:
        from app.services.wrong_date_monitor import _lookup_contact

        if ghl is None:
            from app.adapters.ghl import GHLClient

            ghl = GHLClient(settings=settings)
        _, record = _lookup_contact(ghl, contact_id)
    except RuntimeError as exc:
        if str(exc).startswith("Could not resolve GHL contact"):
            return GateResult("allow", "no GHL contact for this number")
        return _failed(session, job_id, str(exc))
    except Exception as exc:
        return _failed(session, job_id, str(exc))
    tags = cg.parse_tags(_cfg(session, settings, "call_block_tags", cg.DEFAULT_BLOCK_TAGS))
    hit = cg.blocking_reason(record, tags)
    if hit:
        return GateResult("block", hit[0], hit[1])
    return GateResult("allow")


def _failed(session: Session, job_id: str, why: str) -> GateResult:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=2)
    from sqlalchemy import func, select

    from app.models.audit import AuditLog

    tries = session.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "call_gate_lookup_failed", AuditLog.entity_id == job_id, AuditLog.created_at >= since)
    ).scalar() or 0
    _audit(session, "call_gate_lookup_failed", job_id, {"error": why[:200], "attempt": int(tries) + 1})
    return GateResult("give_up" if tries + 1 >= MAX_LOOKUP_ATTEMPTS else "defer", why[:120])


def _audit(session: Session, action: str, entity_id: str, ctx: dict) -> None:
    from app.models.audit import AuditLog

    session.add(AuditLog(id=str(uuid.uuid4()), entity_type="call_gate", entity_id=entity_id, action=action,
                         operator_id="system", context_json=ctx, created_at=datetime.now(tz=timezone.utc)))
    session.flush()


def apply_block(session: Session, job: Any, contact_id: str, result: GateResult) -> None:
    """Cancel this call and every other pending job for the lead; mark the lead closed (and do_not_call when they asked us to stop)."""
    from app.core import intent_actions as ia
    from app.core.campaigns import _cancel_pending_jobs
    from app.worker.claim import cancel_job

    cancel_job(session, job.id)
    cancelled = _cancel_pending_jobs(session, contact_id)
    ia._update_lead_state(session, contact_id, do_not_call=True if result.strong else None, status="closed")
    _audit(session, "call_blocked_ghl_optout", contact_id,
           {"reason": result.reason, "job_id": job.id, "other_jobs_cancelled": cancelled, "do_not_call_set": result.strong})
    logger.warning("call gate: %s - call cancelled | contact_id=%s job_id=%s other_jobs_cancelled=%s",
                   result.reason, contact_id, job.id, cancelled)
