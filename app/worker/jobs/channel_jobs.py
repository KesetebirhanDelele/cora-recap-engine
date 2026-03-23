"""
Channel delivery stub jobs — SMS and email.

Scheduled by intent_actions when a contact requests a channel switch
(request_sms / request_email intents).  Currently stubs: they claim the job,
log the request, and complete cleanly without sending anything.

When real SMS/email delivery is implemented, replace the logger.info body
with the appropriate adapter call and handle exceptions via fail_job /
create_exception as in outbound_jobs.py.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import get_sync_session
from app.worker.claim import claim_job, complete_job, get_worker_id, mark_running

logger = logging.getLogger(__name__)


def send_sms_job(job_id: str) -> None:
    """
    Worker job: deliver an SMS to a contact (stub).

    Payload fields: contact_id, phone.
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_sms_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        logger.info(
            "send_sms_job: stub — SMS delivery not yet implemented | "
            "contact_id=%s phone=%s job_id=%s",
            payload.get("contact_id"), payload.get("phone"), job_id,
        )
        complete_job(session, job)


def send_email_job(job_id: str) -> None:
    """
    Worker job: deliver an email to a contact (stub).

    Payload fields: contact_id, phone (used as identifier until email address
    is added to lead_state).
    """
    settings = get_settings()
    worker_id = get_worker_id()

    with get_sync_session() as session:
        job = claim_job(session, job_id, worker_id=worker_id)
        if job is None:
            logger.info("send_email_job: already claimed | job_id=%s", job_id)
            return

        mark_running(session, job)
        payload = job.payload_json or {}
        logger.info(
            "send_email_job: stub — email delivery not yet implemented | "
            "contact_id=%s job_id=%s",
            payload.get("contact_id"), job_id,
        )
        complete_job(session, job)
