"""
Unit tests for admin routes — Feature 5.

Covers:
  1.  GET /v1/admin/queues: returns queue depths dict with configured queue names
  2.  GET /v1/admin/queues: returns zeros when Redis unavailable
  3.  GET /v1/admin/queues: requires auth — 403 without token
  4.  GET /v1/admin/jobs: returns job list from DB
  5.  GET /v1/admin/jobs: filters by status
  6.  GET /v1/admin/jobs: filters by type
  7.  GET /v1/admin/jobs: pagination via limit/offset
  8.  GET /v1/admin/jobs: requires auth — 403 without token
  9.  GET /v1/admin/jobs: returns empty list when no jobs exist
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings


# ─────────────────────────────────────────────────────────────────────────────
# App fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def test_settings():
    return Settings(
        _env_file=None,
        secret_key="test-secret",
        app_env="development",
        shadow_mode_enabled=True,
        ghl_write_mode="shadow",
        rq_default_queue="default",
        rq_ai_queue="ai",
        rq_callback_queue="callbacks",
        rq_retry_queue="retries",
    )


@pytest.fixture(scope="module")
def client(test_settings):
    from app.config import get_settings
    from unittest.mock import patch as _patch

    with _patch("app.config.get_settings", return_value=test_settings):
        from app.main import create_app
        application = create_app()
        with TestClient(application, raise_server_exceptions=True) as c:
            yield c


AUTH = {"Authorization": "Bearer test-secret"}
NO_AUTH: dict = {}


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/admin/queues
# ─────────────────────────────────────────────────────────────────────────────

def test_get_queues_returns_depth_per_queue(client):
    """Returns a dict with all configured queue names.

    We make Redis unavailable so the route returns zeros — the important
    assertion is that all four configured queue names appear in the response.
    """
    with patch("redis.from_url", side_effect=ConnectionError("no redis")):
        resp = client.get("/v1/admin/queues", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert "queues" in body
    for name in ("default", "ai", "callbacks", "retries"):
        assert name in body["queues"]


def test_get_queues_returns_zeros_when_redis_unavailable(client):
    """Redis connection failure → all queues reported as 0, no error raised."""
    with patch("redis.from_url", side_effect=ConnectionError("redis down")):
        resp = client.get("/v1/admin/queues", headers=AUTH)

    assert resp.status_code == 200
    for depth in resp.json()["queues"].values():
        assert depth == 0


def test_get_queues_requires_auth(client):
    resp = client.get("/v1/admin/queues", headers=NO_AUTH)
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# GET /v1/admin/jobs
# ─────────────────────────────────────────────────────────────────────────────

def _fake_job(job_type="process_call_event", status="completed") -> MagicMock:
    """Build a MagicMock that looks like a ScheduledJob row."""
    j = MagicMock()
    j.id = str(uuid.uuid4())
    j.job_type = job_type
    j.entity_type = "call"
    j.entity_id = "test-entity"
    j.status = status
    j.run_at = datetime.now(tz=timezone.utc)
    j.created_at = datetime.now(tz=timezone.utc)
    j.rq_job_id = None
    return j


def _mock_db_session(jobs: list, total: int):
    """
    Return a context-manager mock that yields a session returning the given jobs/total.
    """
    from contextlib import contextmanager

    mock_session = MagicMock()
    mock_session.scalar.return_value = total
    mock_session.scalars.return_value.all.return_value = jobs

    @contextmanager
    def _ctx():
        yield mock_session

    return _ctx


def test_get_jobs_returns_list(client):
    """GET /v1/admin/jobs returns jobs key with list."""
    jobs = [_fake_job("process_call_event", "completed"),
            _fake_job("run_call_analysis", "pending")]

    with patch("app.api.routes.admin.get_sync_session",
               _mock_db_session(jobs, total=2)):
        resp = client.get("/v1/admin/jobs", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" in body
    assert "total" in body
    assert isinstance(body["jobs"], list)
    assert len(body["jobs"]) == 2


def test_get_jobs_filters_by_status(client):
    """status= query filter — response only contains matching jobs."""
    pending_jobs = [_fake_job(status="pending")]

    with patch("app.api.routes.admin.get_sync_session",
               _mock_db_session(pending_jobs, total=1)):
        resp = client.get("/v1/admin/jobs?status=pending", headers=AUTH)

    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert all(j["status"] == "pending" for j in jobs)


def test_get_jobs_filters_by_type(client):
    """type= query filter — response only contains matching jobs."""
    analysis_jobs = [_fake_job("run_call_analysis", "completed")]

    with patch("app.api.routes.admin.get_sync_session",
               _mock_db_session(analysis_jobs, total=1)):
        resp = client.get("/v1/admin/jobs?type=run_call_analysis", headers=AUTH)

    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert all(j["job_type"] == "run_call_analysis" for j in jobs)


def test_get_jobs_pagination(client):
    """limit and offset query params are accepted and response is valid."""
    paged = [_fake_job() for _ in range(2)]

    with patch("app.api.routes.admin.get_sync_session",
               _mock_db_session(paged, total=5)):
        resp = client.get("/v1/admin/jobs?limit=2&offset=0", headers=AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["jobs"]) == 2
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_get_jobs_requires_auth(client):
    resp = client.get("/v1/admin/jobs", headers=NO_AUTH)
    assert resp.status_code == 403


def test_get_jobs_empty_when_no_jobs(client):
    """Returns empty list when no jobs match."""
    with patch("app.api.routes.admin.get_sync_session",
               _mock_db_session([], total=0)):
        resp = client.get("/v1/admin/jobs?status=failed", headers=AUTH)

    assert resp.status_code == 200
    assert resp.json()["jobs"] == []
    assert resp.json()["total"] == 0
