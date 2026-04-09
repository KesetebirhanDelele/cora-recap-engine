"""
Worker service entrypoint — Phase 6.

Starts RQ workers for all defined queues and registers job functions.
Canonical job state lives in Postgres; Redis/RQ is the execution rail only.

Queue topology:
  default         — process_call_event, process_voicemail_tier
  ai              — run_call_analysis / classify_call_event
  callbacks       — create_crm_task, send_student_summary, launch_outbound_call
  retries         — retry_failed_job
  sheet_mirror    — sync_sheet_rows (Phase 9, out of scope)

Worker role selection (WORKER_ROLE env var):
  default    — listens on the default queue; owns the scheduler loop and
               startup tasks (nurture scheduler, metrics scheduler).
               Only ONE role should own these to avoid duplicate enqueues.
  ai         — listens on the ai queue; runs OpenAI analysis jobs.
  callbacks  — listens on the callbacks queue; runs GHL/Synthflow jobs.
  retries    — listens on the retries queue; isolated from live job traffic.
  all        — listens on all queues (default for single-process deployments
               and backwards-compatible local runs). Also owns scheduler loop.

Worker class selection:
  Windows: SimpleWorker (thread-based; fork() unavailable on Windows)
  Linux/macOS: Worker (fork-based; better isolation for production)

Job recovery:
  On worker startup, any scheduled_jobs with status='pending' and
  run_at <= now are re-enqueued. This handles Redis clears and restarts.
  Only the scheduler-host role (default / all) runs this recovery loop.

Claim/lease:
  Each job function atomically claims its ScheduledJob row before executing.
  Workers that crash mid-job leave a claimed row with an expiring lease.
  The recovery loop detects expired leases and resets them to 'pending'.
"""
from __future__ import annotations

import logging
import os
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
    "launch_outbound_call":  "rq_callback_queue",
    "run_call_analysis":     "rq_ai_queue",
    "classify_call_event":   "rq_ai_queue",
    "create_crm_task":       "rq_callback_queue",
    "send_student_summary":  "rq_callback_queue",
    "update_lead_state":     "rq_default_queue",
    "run_nurture_scheduler": "rq_default_queue",
    "send_sms":              "rq_default_queue",
    "send_email":            "rq_default_queue",
    "collect_metrics":       "rq_default_queue",
}

# Maps WORKER_ROLE value → list of settings attributes for the queues to listen on.
# "all" preserves the original single-worker topology for local/dev use.
_ROLE_QUEUE_ATTRS: dict[str, list[str]] = {
    "default":   ["rq_default_queue"],
    "ai":        ["rq_ai_queue"],
    "callbacks": ["rq_callback_queue"],
    "retries":   ["rq_retry_queue"],
    "all": [
        "rq_default_queue",
        "rq_ai_queue",
        "rq_callback_queue",
        "rq_retry_queue",
        "rq_sheet_mirror_queue",
    ],
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


def get_queues_for_role(role: str, settings=None) -> list[str]:
    """Return the list of RQ queue names for the given worker role."""
    if settings is None:
        settings = get_settings()
    attrs = _ROLE_QUEUE_ATTRS.get(role, _ROLE_QUEUE_ATTRS["all"])
    return [getattr(settings, attr) for attr in attrs]


def get_queues() -> list[str]:
    """Return all queue names. Kept for backwards compatibility."""
    return get_queues_for_role("all")


def get_job_registry() -> dict[str, object]:
    """Return mapping of job_type → job function for the worker dispatcher."""
    from app.worker.jobs.ai_jobs import classify_call_event, run_call_analysis
    from app.worker.jobs.call_processing import process_call_event
    from app.worker.jobs.channel_jobs import send_email_job, send_sms_job
    from app.worker.jobs.crm_jobs import create_crm_task, send_student_summary
    from app.worker.jobs.lifecycle_jobs import update_lead_state
    from app.worker.jobs.metrics_jobs import collect_metrics_job
    from app.worker.jobs.nurture_scheduler import run_nurture_scheduler
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
        # Nurture graduation
        "run_nurture_scheduler": run_nurture_scheduler,
        # Channel delivery stubs
        "send_sms": send_sms_job,
        "send_email": send_email_job,
        # Dashboard metrics collection
        "collect_metrics": collect_metrics_job,
    }


def run() -> None:
    """Start RQ workers listening on queues determined by WORKER_ROLE.

    WORKER_ROLE controls which queues this process listens on and whether it
    owns the scheduler loop. Only the 'default' and 'all' roles run the
    scheduler loop — running it in every process would cause duplicate RQ
    enqueues for the same pending Postgres jobs (safe due to claim/lease, but
    wasteful and noisy in logs).

    Set WORKER_ROLE=all (or omit it) for local single-process runs.
    In production, start separate processes with distinct roles.
    """
    role = os.environ.get("WORKER_ROLE", "all").lower()
    if role not in _ROLE_QUEUE_ATTRS:
        logger.error(
            "Unknown WORKER_ROLE=%r — valid values: %s",
            role, ", ".join(_ROLE_QUEUE_ATTRS),
        )
        sys.exit(1)

    settings = get_settings()
    queues = get_queues_for_role(role, settings)

    # Only the default/all role owns the scheduler loop and startup tasks.
    # This prevents multiple processes from racing to enqueue the same jobs.
    is_scheduler_host = role in ("default", "all")

    logger.info(
        "Cora worker starting | env=%s role=%s queues=%s shadow_mode=%s scheduler_host=%s",
        settings.app_env,
        role,
        queues,
        settings.shadow_mode_enabled,
        is_scheduler_host,
    )

    if is_scheduler_host:
        # Ensure the periodic nurture scheduler has a pending job on startup.
        try:
            from app.worker.jobs.nurture_scheduler import ensure_scheduled
            ensure_scheduled(settings)
            logger.info("Nurture scheduler ensured on startup")
        except Exception as exc:
            logger.warning("Could not ensure nurture scheduler on startup: %s", exc)

        # Ensure the metrics collection job is scheduled on startup.
        try:
            from app.worker.jobs.metrics_jobs import start_metrics_scheduler
            start_metrics_scheduler()
            logger.info("Metrics scheduler ensured on startup")
        except Exception as exc:
            logger.warning("Could not ensure metrics scheduler on startup: %s", exc)

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

        if is_scheduler_host:
            # Build a full queue map across ALL queue names so the scheduler
            # loop can route any job type to its target queue — regardless of
            # which queues this worker process actually listens on.
            all_queue_names = get_queues_for_role("all", settings)
            all_queues_map = {
                name: Queue(name=name, connection=redis_conn)
                for name in all_queue_names
            }
            registry = get_job_registry()
            job_type_queues = {
                jt: all_queues_map.get(getattr(settings, attr, settings.rq_default_queue))
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
