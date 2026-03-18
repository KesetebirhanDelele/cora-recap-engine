"""
Worker service entrypoint — Phase 6.

Starts RQ workers for all defined queues and registers job functions.
Canonical job state lives in Postgres; Redis/RQ is the execution rail only.

Queue topology:
  default         — process_call_event, process_voicemail_tier
  ai              — run_call_analysis
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
import threading
import time
from datetime import datetime, timezone

import app.compat  # noqa: F401 — Windows fork→spawn patch; must precede rq imports

from app.config import get_settings

logger = logging.getLogger(__name__)

# Maps job_type → settings attribute that holds the queue name.
# Used by the scheduler loop to route due jobs to the correct RQ queue.
_JOB_QUEUE_ATTRS: dict[str, str] = {
    "process_call_event":    "rq_default_queue",
    "process_voicemail_tier": "rq_default_queue",
    "launch_outbound_call":  "rq_default_queue",
    "run_call_analysis":     "rq_ai_queue",
    "classify_call_event":   "rq_ai_queue",
    "create_crm_task":       "rq_default_queue",
    "send_student_summary":  "rq_default_queue",
    "update_lead_state":     "rq_default_queue",
}


def _run_scheduler_loop(
    job_type_queues: dict,
    job_registry: dict,
    interval_seconds: int = 30,
) -> None:
    """
    Background daemon thread: scan Postgres for due pending jobs and enqueue in RQ.

    Runs every interval_seconds. Picks up:
      - Future-dated jobs whose run_at has now passed (e.g. voicemail retry calls)
      - Jobs that survived a Redis clear or worker restart

    This is the component that makes delayed scheduling work. schedule_job() only
    enqueues in RQ immediately when run_at <= now at creation time. For future jobs
    (run_at = now + N minutes), this loop is what eventually triggers them.
    """
    from sqlalchemy import select

    from app.db import get_sync_session
    from app.models.scheduled_job import ScheduledJob
    from app.worker.scheduler import enqueue_now

    logger.info("scheduler_loop: started | interval=%ds", interval_seconds)

    while True:
        try:
            with get_sync_session() as session:
                now = datetime.now(tz=timezone.utc)
                due_jobs = session.scalars(
                    select(ScheduledJob)
                    .where(
                        ScheduledJob.status == "pending",
                        ScheduledJob.run_at <= now,
                    )
                    .limit(100)
                ).all()

                if due_jobs:
                    logger.info("scheduler_loop: found %d due job(s)", len(due_jobs))

                for job in due_jobs:
                    rq_queue = job_type_queues.get(job.job_type)
                    job_func = job_registry.get(job.job_type)

                    if rq_queue is None or job_func is None:
                        logger.warning(
                            "scheduler_loop: no handler for job_type=%r | job_id=%s",
                            job.job_type, job.id,
                        )
                        continue

                    enqueue_now(session, job, rq_queue, job_func)

        except Exception as exc:
            logger.exception("scheduler_loop: error | %s", exc)

        time.sleep(interval_seconds)


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

        # Build job_type → Queue map for the scheduler loop
        queue_by_name = {q.name: q for q in qs}
        registry = get_job_registry()
        job_type_queues = {
            jt: queue_by_name.get(getattr(settings, attr, settings.rq_default_queue))
            for jt, attr in _JOB_QUEUE_ATTRS.items()
        }

        # Start the scheduler polling loop in a daemon thread.
        # This picks up delayed jobs (e.g. voicemail retries) once run_at arrives.
        t = threading.Thread(
            target=_run_scheduler_loop,
            args=(job_type_queues, registry),
            daemon=True,
            name="cora-scheduler-loop",
        )
        t.start()
        logger.info("Scheduler loop started (daemon thread)")

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
