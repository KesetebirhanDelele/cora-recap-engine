"""
Operator observability routes — admin view of queue and job state.

All routes require: Authorization: Bearer {SECRET_KEY}
Optional header:   X-Operator-Id: {your-name}

Endpoints:
  GET /v1/admin/queues — RQ queue depths (pending job count) per configured queue
  GET /v1/admin/jobs   — Scheduled job list from Postgres (filterable by status, type)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.api.deps import DashboardAuth
from app.db import get_sync_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/queues")
def get_queue_depths(auth: DashboardAuth) -> dict:
    """
    Return current pending depth per RQ queue.

    Reads live data from Redis when available.
    Returns zeros when Redis is offline rather than raising.
    """
    from app.config import get_settings
    settings = get_settings()

    queue_names = [
        settings.rq_default_queue,
        settings.rq_ai_queue,
        settings.rq_callback_queue,
        settings.rq_retry_queue,
    ]

    depths: dict[str, int] = {}

    try:
        import redis as redis_lib
        from rq import Queue

        url = (
            settings.redis_url
            or f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
        )
        ssl_kwargs = {"ssl_cert_reqs": None} if url.startswith("rediss://") else {}
        auth_kwargs: dict = {}
        if settings.redis_username:
            auth_kwargs["username"] = settings.redis_username
        if settings.redis_password:
            auth_kwargs["password"] = settings.redis_password

        conn = redis_lib.from_url(url, **ssl_kwargs, **auth_kwargs)
        for name in queue_names:
            q = Queue(name, connection=conn)
            depths[name] = len(q)

    except Exception as exc:
        logger.warning("get_queue_depths: Redis unavailable | %s", exc)
        for name in queue_names:
            depths[name] = 0

    return {"queues": depths}


@router.get("/jobs")
def list_jobs(
    auth: DashboardAuth,
    job_status: Optional[str] = Query(None, alias="status",
                                       description="pending|running|completed|failed|cancelled"),
    job_type: Optional[str] = Query(None, alias="type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    List scheduled jobs from Postgres with optional filters.

    Filters:
      status — pending | running | completed | failed | cancelled
      type   — job_type string (e.g. process_call_event, run_call_analysis)
    """
    from sqlalchemy import func, select

    from app.models.scheduled_job import ScheduledJob

    with get_sync_session() as session:
        base = select(ScheduledJob)
        if job_status:
            base = base.where(ScheduledJob.status == job_status)
        if job_type:
            base = base.where(ScheduledJob.job_type == job_type)

        total_q = select(func.count()).select_from(ScheduledJob)
        if job_status:
            total_q = total_q.where(ScheduledJob.status == job_status)
        if job_type:
            total_q = total_q.where(ScheduledJob.job_type == job_type)

        total = session.scalar(total_q) or 0
        jobs = session.scalars(
            base.order_by(ScheduledJob.created_at.desc()).offset(offset).limit(limit)
        ).all()

    return {
        "jobs": [
            {
                "id": j.id,
                "job_type": j.job_type,
                "entity_type": j.entity_type,
                "entity_id": j.entity_id,
                "status": j.status,
                "run_at": j.run_at.isoformat() if j.run_at else None,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "rq_job_id": j.rq_job_id,
            }
            for j in jobs
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
