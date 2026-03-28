"""
Outbound call launch job — runs on the `default` RQ queue.

Executes the Synthflow Make Call workflow for a scheduled outbound call.
This job is enqueued by POST /v1/test/calls/outbound and by the production
outbound call scheduler.

Job lifecycle:
  1. Claim the ScheduledJob row
  2. Read phone, lead_name, campaign_name from payload
  3. Call SynthflowClient.launch_new_lead_call()
  4. Log the result and complete the job
  5. On failure: create exception record, fail the job

The Synthflow call completion arrives separately via:
  POST /v1/webhooks/calls (completed-call webhook)
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, fail_job, get_worker_id, mark_running
from app.worker.exceptions import create_exception

logger = logging.getLogger(__name__)


def launch_outbound_call_job(job_id: str) -> None:
    """
    Worker job: invoke Synthflow Make Call workflow.

    Reads job payload, calls SynthflowClient.launch_new_lead_call(),
    logs the result. Call completion arrives via webhook callback.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("launch_outbound_call_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        phone = payload.get("phone_number", "")
        lead_name = payload.get("lead_name", "")
        campaign_name = payload.get("campaign_name", "New_Lead")
        correlation_id = payload.get("correlation_id", job_id)

        # ── Shadow mode: log and skip the real Synthflow call ─────────────────
        if settings.shadow_mode_enabled:
            from app.worker.shadow import log_shadow_action
            log_shadow_action(
                session,
                contact_id=payload.get("contact_id") or phone,
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
                entity_type="test_call",
                entity_id=correlation_id,
            )
            fail_job(session, job, reason=str(exc))
            raise
