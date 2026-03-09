"""
Phase 8 — dashboard route tests.

Uses FastAPI TestClient with service-layer mocks.
DB is never touched — all service calls are patched at the route level.

Key: conftest.py sets SECRET_KEY=test-secret, so auth token is "test-secret".

Covers:
  1.  GET /v1/exceptions — returns 403 without auth
  2.  GET /v1/exceptions — returns 200 with valid auth
  3.  GET /v1/exceptions/{id} — returns 404 for unknown id
  4.  GET /v1/exceptions/{id} — returns 200 for known id
  5.  POST retry-now — returns 403 without auth
  6.  POST retry-now — returns 202 on success
  7.  POST retry-now — returns 409 on conflict
  8.  POST retry-delay — returns 202 with delay_minutes
  9.  POST retry-delay — returns 409 on conflict
  10. POST cancel-future-jobs — returns 200 with count
  11. POST cancel-future-jobs — returns 409 on conflict
  12. POST force-finalize — returns 200 on success
  13. POST force-finalize — returns 409 on conflict
  14. X-Operator-Id header passed as operator_id to service
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# conftest.py sets SECRET_KEY=test-secret — use that for auth
def _auth(operator_id: str = "test-op") -> dict:
    return {"Authorization": "Bearer test-secret", "X-Operator-Id": operator_id}


@contextmanager
def _no_db():
    """Null context manager — replaces get_sync_session so no DB is needed."""
    yield MagicMock()


_EXCEPTION_ROW = {
    "id": "exc-001", "type": "ghl_auth", "severity": "critical", "status": "open",
    "entity_type": "call", "entity_id": "call-123", "call_event_id": None,
    "context_json": {}, "version": 0, "resolution_reason": None, "resolved_by": None,
    "created_at": "2026-03-09T00:00:00+00:00", "updated_at": "2026-03-09T00:00:00+00:00",
}
_DETAIL_ROW = {**_EXCEPTION_ROW, "audit_trail": []}
_LIST = {"exceptions": [_EXCEPTION_ROW], "total": 1, "limit": 20, "offset": 0}

_SVC = "app.services.dashboard"
_SES = "app.api.routes.exceptions.get_sync_session"


# ─────────────────────────────────────────────────────────────────────────────
# 1-2. Auth
# ─────────────────────────────────────────────────────────────────────────────

def test_list_requires_auth(client):
    resp = client.get("/v1/exceptions")
    assert resp.status_code == 403


def test_list_wrong_token_403(client):
    resp = client.get("/v1/exceptions", headers={"Authorization": "Bearer bad"})
    assert resp.status_code == 403


def test_list_returns_200(client):
    with patch(_SVC + ".list_exceptions", return_value=_LIST), \
         patch(_SES, return_value=_no_db()):
        resp = client.get("/v1/exceptions", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert "exceptions" in data


# ─────────────────────────────────────────────────────────────────────────────
# 3-4. GET detail
# ─────────────────────────────────────────────────────────────────────────────

def test_get_exception_404(client):
    with patch(_SVC + ".get_exception_detail", return_value=None), \
         patch(_SES, return_value=_no_db()):
        resp = client.get("/v1/exceptions/unknown", headers=_auth())
    assert resp.status_code == 404


def test_get_exception_200(client):
    with patch(_SVC + ".get_exception_detail", return_value=_DETAIL_ROW), \
         patch(_SES, return_value=_no_db()):
        resp = client.get("/v1/exceptions/exc-001", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["id"] == "exc-001"


# ─────────────────────────────────────────────────────────────────────────────
# 5-7. retry-now
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_now_requires_auth(client):
    resp = client.post("/v1/exceptions/exc-001/retry-now")
    assert resp.status_code == 403


def test_retry_now_202(client):
    with patch(_SVC + ".retry_now", return_value={"success": True, "new_job_id": "j1"}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/retry-now", headers=_auth())
    assert resp.status_code == 202
    assert resp.json()["success"] is True


def test_retry_now_409_on_conflict(client):
    with patch(_SVC + ".retry_now",
               return_value={"conflict": True, "reason": "not open"}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/retry-now", headers=_auth())
    assert resp.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 8-9. retry-delay
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_delay_202(client):
    payload = {"success": True, "new_job_id": "j2", "run_at": "2026-03-09T01:00:00+00:00"}
    with patch(_SVC + ".retry_with_delay", return_value=payload), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/retry-delay",
                           json={"delay_minutes": 60}, headers=_auth())
    assert resp.status_code == 202


def test_retry_delay_409_on_conflict(client):
    with patch(_SVC + ".retry_with_delay",
               return_value={"conflict": True, "reason": "not open"}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/retry-delay",
                           json={"delay_minutes": 30}, headers=_auth())
    assert resp.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 10-11. cancel-future-jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_cancel_future_jobs_200(client):
    payload = {"success": True, "cancelled_count": 2, "cancelled_ids": ["j1", "j2"]}
    with patch(_SVC + ".cancel_future_jobs", return_value=payload), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/cancel-future-jobs", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["cancelled_count"] == 2


def test_cancel_future_jobs_409_on_conflict(client):
    with patch(_SVC + ".cancel_future_jobs",
               return_value={"conflict": True, "reason": "not open"}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/cancel-future-jobs", headers=_auth())
    assert resp.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 12-13. force-finalize
# ─────────────────────────────────────────────────────────────────────────────

def test_force_finalize_200(client):
    with patch(_SVC + ".force_finalize",
               return_value={"success": True, "cancelled_jobs": 1, "lead_finalized": False}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/force-finalize", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_force_finalize_409_on_conflict(client):
    with patch(_SVC + ".force_finalize",
               return_value={"conflict": True, "reason": "already resolved"}), \
         patch(_SES, return_value=_no_db()):
        resp = client.post("/v1/exceptions/exc-001/force-finalize", headers=_auth())
    assert resp.status_code == 409


# ─────────────────────────────────────────────────────────────────────────────
# 14. X-Operator-Id propagated
# ─────────────────────────────────────────────────────────────────────────────

def test_operator_id_passed_from_header(client):
    captured = {}

    def _capture(session, exception_id, operator_id):
        captured["operator_id"] = operator_id
        return {"success": True, "new_job_id": "j-cap"}

    with patch(_SVC + ".retry_now", side_effect=_capture), \
         patch(_SES, return_value=_no_db()):
        client.post("/v1/exceptions/exc-001/retry-now",
                    headers=_auth(operator_id="alice"))

    assert captured.get("operator_id") == "alice"
