"""
Outbound call launch job — runs on the `default` RQ queue.

Executes the Synthflow Make Call workflow for a scheduled outbound call.
This job is enqueued by POST /v1/test/calls/outbound and by the production
outbound call scheduler.

Job lifecycle:
  1. Claim the ScheduledJob row
  2. Read phone, lead_name, campaign_name from payload
  3. Check campaign active window in caller's local timezone (live mode only).
     If outside window: cancel current job, reschedule at next window-open time.
  4. Call SynthflowClient.launch_new_lead_call()
  5. Log the result and complete the job
  6. On failure: create exception record, fail the job

The Synthflow call completion arrives separately via:
  POST /v1/webhooks/calls (completed-call webhook)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running, release_job_to_pending
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)


def launch_outbound_call_job(job_id: str) -> None:
    """
    Worker job: invoke Synthflow Make Call workflow.

    Reads job payload, checks campaign active window in the caller's local
    timezone, then calls SynthflowClient.launch_new_lead_call(). Call
    completion arrives via webhook callback.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("launch_outbound_call_job: already claimed | job_id=%s", job_id)
            return

        # ── System pause check ────────────────────────────────────────────────
        from app.core.mode_flags import get_mode_flags
        flags = get_mode_flags(session, settings)
        if flags.system_paused:
            logger.info(
                "launch_outbound_call_job: system paused — releasing | job_id=%s", job_id
            )
            release_job_to_pending(session, job)
            session.commit()
            return

        # Load payload before mark_running so the window check can cancel
        # the job while it is still in 'claimed' status (cancel_job requires
        # pending or claimed).
        payload = job.payload_json or {}
        phone = payload.get("phone_number", "")
        lead_name = payload.get("lead_name", "")
        campaign_name = payload.get("campaign_name", "New_Lead")
        correlation_id = payload.get("correlation_id", job_id)
        contact_id = payload.get("contact_id") or phone

        # ── Campaign active-window check (live mode only) ─────────────────────
        # Shadow mode skips this — no real outbound action is taken so there
        # is nothing to defer.
        if not flags.shadow_mode_enabled:
            from app.core.campaign_schedule import (
                get_contact_timezone,
                is_campaign_active,
                next_active_window_start,
            )
            from app.worker.claim import cancel_job
            from app.worker.scheduler import schedule_job

            now = datetime.now(tz=timezone.utc)
            contact_tz = get_contact_timezone(session, contact_id, settings)
            if not is_campaign_active(campaign_name, now, settings, contact_tz, session):
                next_open = next_active_window_start(campaign_name, now, settings, contact_tz, session)
                logger.info(
                    "launch_outbound_call_job: outside active window — deferring | "
                    "campaign=%s contact_tz=%s job_id=%s rescheduled_for=%s",
                    campaign_name, contact_tz, job_id, next_open.isoformat(),
                )
                cancel_job(session, job.id)
                schedule_job(
                    session=session,
                    job_type="launch_outbound_call",
                    entity_type=job.entity_type,
                    entity_id=job.entity_id,
                    run_at=next_open,
                    payload=payload,
                )
                return

        mark_running(session, job)

        # ── Shadow mode: log and skip the real Synthflow call ─────────────────
        if flags.shadow_mode_enabled:
            from app.worker.shadow import log_shadow_action
            log_shadow_action(
                session,
                contact_id=contact_id,
                action_type="outbound_call",
                payload={
                    "run_at": job.run_at.isoformat() if job.run_at else None,
                    "campaign": campaign_name,
                    "phone": phone,
                    "lead_name": lead_name,
                    "correlation_id": correlation_id,
                },
            )
            complete_job(session, job)
            return

        try:
            from app.adapters.synthflow import SynthflowClient

            client = SynthflowClient(settings=settings)
            result = client.launch_new_lead_call(
                phone=phone,
                lead_name=lead_name,
                campaign_name=campaign_name,
                metadata={
                    "correlation_id": correlation_id,
                    "job_id": job_id,
                    "source": payload.get("source", "e2e_test_harness"),
                },
            )
            logger.info(
                "launch_outbound_call_job: Synthflow call launched | "
                "correlation_id=%s job_id=%s result=%s",
                correlation_id, job_id, result,
            )
            complete_job(session, job)

        except Exception as exc:
            logger.exception(
                "launch_outbound_call_job: error | job_id=%s: %s", job_id, exc
            )
            create_exception(
                session,
                type="outbound_launch_failed",
                severity="critical",
                context={
                    "job_id": job_id,
                    "correlation_id": correlation_id,
                    "error": str(exc),
                },
                entity_type="lead",
                entity_id=contact_id,
            )
            fail_job(session, job, reason=str(exc))
            session.commit()
            raise
