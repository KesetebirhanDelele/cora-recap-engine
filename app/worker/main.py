"""
Worker service entrypoint — Phase 6.

Starts RQ workers for all defined queues and registers job functions.
Canonical job state lives in Postgres; Redis/RQ is the execution rail only.

Queue topology:
  default         — process_call_event, process_voicemail_tier
  ai              — run_call_analysis
  callbacks       — schedule_synthflow_callback (Phase 7)
  retries         — retry_failed_job
  sheet_mirror    — sync_sheet_rows (Phase 9, out of scope)

Worker class selection:
  Windows: SimpleWorker (thread-based; fork() unavailable on Windows)
  Linux/macOS: Worker (fork-based; better isolation for production)

Job recovery:
  On worker startup, any scheduled_jobs with status='pending' and
  run_at <= now are re-enqueued. This handles Redis clears and restarts.

Claim/lease:
  Each job function atomically claims its ScheduledJob row before executing.
  Workers that crash mid-job leave a claimed row with an expiring lease.
  The recovery loop detects expired leases and resets them to 'pending'.
"""
from __future__ import annotations

import logging
import platform
import sys

import app.compat  # noqa: F401 — Windows fork→spawn patch; must precede rq imports

from app.config import get_settings

logger = logging.getLogger(__name__)


def get_queues() -> list[str]:
    settings = get_settings()
    return [
        settings.rq_default_queue,
        settings.rq_ai_queue,
        settings.rq_callback_queue,
        settings.rq_retry_queue,
        settings.rq_sheet_mirror_queue,
    ]


def get_job_registry() -> dict[str, object]:
    """Return mapping of job_type → job function for the worker dispatcher."""
    from app.worker.jobs.ai_jobs import classify_call_event, run_call_analysis
    from app.worker.jobs.call_processing import process_call_event
    from app.worker.jobs.crm_jobs import create_crm_task, send_student_summary
    from app.worker.jobs.lifecycle_jobs import update_lead_state
    from app.worker.jobs.outbound_jobs import launch_outbound_call_job
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    return {
        "process_call_event": process_call_event,
        "run_call_analysis": run_call_analysis,        # backwards compat
        "classify_call_event": classify_call_event,    # canonical name
        "process_voicemail_tier": process_voicemail_tier,
        "launch_outbound_call": launch_outbound_call_job,
        # Feature 2+3: CRM / recap delivery
        "create_crm_task": create_crm_task,
        "send_student_summary": send_student_summary,
        # Feature 4: lead lifecycle
        "update_lead_state": update_lead_state,
    }


def run() -> None:
    """Start RQ workers listening on all queues."""
    settings = get_settings()
    queues = get_queues()

    logger.info(
        "Cora worker starting | env=%s queues=%s shadow_mode=%s",
        settings.app_env,
        queues,
        settings.shadow_mode_enabled,
    )

    try:
        import redis
        from rq import Queue, SimpleWorker, Worker

        url = (
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )
        # rediss:// (SSL) connections to Redis Cloud require ssl_cert_reqs=None
        # to bypass certificate hostname verification — common with Redis Cloud
        # endpoints that use self-signed or intermediate CA certs.
        ssl_kwargs = {"ssl_cert_reqs": None} if url.startswith("rediss://") else {}
        # Explicit username/password settings override any credentials embedded
        # in REDIS_URL (e.g. Redis Cloud URLs that omit auth or use defaults).
        auth_kwargs: dict = {}
        if settings.redis_username:
            auth_kwargs["username"] = settings.redis_username
        if settings.redis_password:
            auth_kwargs["password"] = settings.redis_password
        redis_conn = redis.from_url(url, **ssl_kwargs, **auth_kwargs)

        qs = [Queue(name=q, connection=redis_conn) for q in queues]

        # Windows does not support fork(); use SimpleWorker (thread-based).
        # Linux/macOS use the standard fork-based Worker for better isolation.
        worker_cls = SimpleWorker if platform.system() == "Windows" else Worker
        logger.info("Worker class: %s (platform=%s)", worker_cls.__name__, platform.system())

        worker = worker_cls(qs, connection=redis_conn)
        worker.work()

    except Exception as exc:
        logger.exception("Worker failed to start: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run()
