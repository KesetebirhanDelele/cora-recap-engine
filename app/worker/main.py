"""
Worker service entrypoint — Phase 6.

Starts RQ workers for all defined queues and registers job functions.
Canonical job state lives in Postgres; Redis/RQ is the execution rail only.

Queue topology:
  default         — process_call_event, process_voicemail_tier
  ai              — run_call_analysis
  callbacks       — schedule_synthflow_callback (Phase 7)
  retries         — retry_failed_job
  sheet_mirror    — sync_sheet_rows (Phase 9)

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
import sys

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
    from app.worker.jobs.ai_jobs import run_call_analysis
    from app.worker.jobs.call_processing import process_call_event
    from app.worker.jobs.voicemail_jobs import process_voicemail_tier

    return {
        "process_call_event": process_call_event,
        "run_call_analysis": run_call_analysis,
        "process_voicemail_tier": process_voicemail_tier,
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
        from rq import Queue, Worker

        redis_conn = redis.from_url(
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )

        qs = [Queue(name=q, connection=redis_conn) for q in queues]
        worker = Worker(qs, connection=redis_conn)
        worker.work()

    except Exception as exc:
        logger.exception("Worker failed to start: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    run()
