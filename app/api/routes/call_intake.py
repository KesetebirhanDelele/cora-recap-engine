"""
POST /v1/webhooks/leads/{campaign_type} — GHL-triggered call-launch intake.

Replaces GHL's current direct-to-Synthflow "Send HTTP request" trigger
(spec/21). GHL's New Lead and Cold Lead workflows POST here instead of
hitting Synthflow's Make Call webhook directly, so every call-launch
trigger — GHL-originated or Cora's own internal scheduling — goes through
the same ScheduledJob queue and the same collision-safe 75s bucket grid
(outbound_jobs.py::_compute_window_run_at) instead of GHL's direct trigger
being invisible to it.

campaign_type path param: "new_lead" or "cold_lead" — the same values
campaigns.py::enter_campaign() already accepts. Campaign identity comes
from which GHL action/URL called this endpoint, not a payload field —
GHL's confirmed payload shape (phone/first_name/email, captured 2026-08-23
from the live "Cora Outbound - New Leads" action) has no campaign_name key.

Auth: shared-secret header (X-Cora-Webhook-Secret), checked against
settings.cora_inbound_webhook_secret. This endpoint can trigger a real,
billable outbound call — unlike the unauthenticated /v1/webhooks/calls
completion callback, it must never be reachable without the secret. An
unset secret closes the endpoint entirely rather than accepting requests
unauthenticated.

Reuses campaigns.py::enter_campaign() for everything past find-or-create:
pause-flag checks (system/outbound/cold-lead-only), existing-job
cancellation, the pending-job dedup guard, and slot-aware + priority-aware
scheduling all already live there. This endpoint's only new responsibility
is authenticating the request and resolving/creating the LeadState row.

first_name/email are accepted and logged but not yet forwarded into the
call payload — enter_campaign() currently always schedules with an empty
lead_name (LeadState has no display-name column; see its own docstring).
Wiring first_name through is a natural follow-up, deliberately left out of
this change to keep it scoped to what spec/21 actually requires.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select

from app.config import get_settings
from app.core.campaigns import enter_campaign
from app.db import get_sync_session
from app.models.lead_state import LeadState

logger = logging.getLogger(__name__)

router = APIRouter()

_VALID_CAMPAIGN_TYPES = {"new_lead", "cold_lead"}


def _looks_like_e164(phone: str) -> bool:
    return phone.startswith("+") and len(phone) >= 10


def _record_auth_failure(campaign_type: str, reason: str) -> None:
    """
    Persist an 'intake_auth_failed' exception so an otherwise invisible 401
    storm at this endpoint surfaces as an alert
    (alerting.py::_evaluate_intake_auth_failure).

    Deduplicated to a single open row: a misconfigured caller (GHL retries
    hard) must not flood the exceptions table via this public endpoint, and
    one open row is all the alert needs to stay lit until an operator
    resolves it. Best-effort — any failure here is swallowed, observability
    must never break the endpoint's own response path.
    """
    try:
        from app.models.exception import ExceptionRecord
        from app.worker.exceptions import create_exception

        with get_sync_session() as session:
            already_open = session.scalars(
                select(ExceptionRecord.id).where(
                    ExceptionRecord.type == "intake_auth_failed",
                    ExceptionRecord.status == "open",
                )
            ).first()
            if already_open is not None:
                return
            create_exception(
                session,
                type="intake_auth_failed",
                context={"campaign_type": campaign_type, "reason": reason},
                severity="warning",
                entity_type="webhook",
                entity_id="v1/webhooks/leads",
            )
            session.commit()
    except Exception as exc:  # noqa: BLE001 — deliberately non-fatal
        logger.warning("intake_lead: could not record auth-failure exception: %s", exc)


@router.post("/leads/{campaign_type}", status_code=status.HTTP_202_ACCEPTED)
async def intake_lead(
    campaign_type: str,
    request: Request,
    x_cora_webhook_secret: str | None = Header(default=None),
):
    settings = get_settings()

    # ── Auth ─────────────────────────────────────────────────────────────────
    # An unset secret means the endpoint is closed, not open — never treat a
    # missing configured value as "auth not required".
    configured_secret = settings.cora_inbound_webhook_secret
    if not configured_secret:
        reason = "server_secret_not_configured"
    elif x_cora_webhook_secret != configured_secret:
        reason = "missing_or_invalid_header"
    else:
        reason = None

    if reason is not None:
        logger.warning(
            "intake_lead: rejected — %s | campaign_type=%s", reason, campaign_type,
        )
        _record_auth_failure(campaign_type, reason)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing webhook secret",
        )

    if campaign_type not in _VALID_CAMPAIGN_TYPES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown campaign_type: {campaign_type!r}",
        )

    body = await request.json()
    phone = (body.get("phone") or "").strip()
    if not _looks_like_e164(phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone is required and must be E.164 (e.g. +17865551234)",
        )

    first_name = (body.get("first_name") or "").strip()
    email = (body.get("email") or "").strip()

    with get_sync_session() as session:
        lead = session.scalars(
            select(LeadState).where(LeadState.contact_id == phone)
        ).first()

        if lead is None:
            # Brand-new contact GHL has never sent to Cora before — phone
            # itself is the contact_id, matching this repo's established
            # phone-derived-contact_id convention (see ghl_integration
            # project memory / lifecycle_jobs.py's identical pattern).
            now = datetime.now(tz=timezone.utc)
            lead = LeadState(
                id=str(uuid.uuid4()),
                contact_id=phone,
                normalized_phone=phone,
                version=0,
                created_at=now,
                updated_at=now,
            )
            session.add(lead)
            session.flush()
            logger.info(
                "intake_lead: created new lead_state | contact_id=%s campaign_type=%s",
                phone, campaign_type,
            )

        # enter_campaign() owns: pause-flag checks, cancelling any existing
        # pending/claimed jobs, resetting the voicemail tier, the pending-job
        # dedup guard, and slot-aware/priority-aware scheduling.
        enter_campaign(session, lead, campaign_type, settings=settings)
        session.commit()

    logger.info(
        "intake_lead: accepted | contact_id=%s campaign_type=%s first_name=%r has_email=%s",
        phone, campaign_type, first_name, bool(email),
    )
    return {"status": "accepted", "campaign_type": campaign_type}
