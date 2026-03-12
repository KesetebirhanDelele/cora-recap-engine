"""
Unit tests for Synthflow webhook payload normalization.

Covers:
  1.  call_id lowercase accepted unchanged
  2.  Call_id (capital C) mapped to call_id
  3.  callId (camelCase) mapped to call_id
  4.  duration mapped to duration_seconds when duration_seconds absent
  5.  duration_seconds preserved when already present (no overwrite)
  6.  direction defaults to 'outbound' when absent
  7.  direction preserved when already present
  8.  original Synthflow keys are preserved in normalized payload (additive)
  9.  empty call_id aliases are skipped (falsy guard)
  10. all fields absent → call_id still missing (returns empty string)
  11. webhook route returns 202 with Call_id payload (integration)
  12. webhook route returns 422 with no call_id at all
  13. webhook route logs original payload keys on 422
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.routes.webhooks import normalize_synthflow_payload


# ─────────────────────────────────────────────────────────────────────────────
# 1–10: normalize_synthflow_payload unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_lowercase_call_id_unchanged():
    payload = {"call_id": "abc-123", "duration": 60}
    result = normalize_synthflow_payload(payload)
    assert result["call_id"] == "abc-123"


def test_capital_Call_id_mapped():
    payload = {"Call_id": "abc-456", "duration": 45}
    result = normalize_synthflow_payload(payload)
    assert result["call_id"] == "abc-456"


def test_camelCase_callId_mapped():
    payload = {"callId": "abc-789"}
    result = normalize_synthflow_payload(payload)
    assert result["call_id"] == "abc-789"


def test_duration_mapped_to_duration_seconds():
    payload = {"call_id": "x", "duration": 90}
    result = normalize_synthflow_payload(payload)
    assert result["duration_seconds"] == 90


def test_duration_seconds_not_overwritten():
    payload = {"call_id": "x", "duration": 90, "duration_seconds": 95}
    result = normalize_synthflow_payload(payload)
    assert result["duration_seconds"] == 95  # original preserved


def test_direction_defaults_to_outbound():
    payload = {"call_id": "x"}
    result = normalize_synthflow_payload(payload)
    assert result["direction"] == "outbound"


def test_direction_preserved_when_present():
    payload = {"call_id": "x", "direction": "inbound"}
    result = normalize_synthflow_payload(payload)
    assert result["direction"] == "inbound"


def test_original_keys_preserved_alongside_normalized():
    payload = {"Call_id": "abc", "duration": 60, "transcript": "hello"}
    result = normalize_synthflow_payload(payload)
    # Normalized aliases added
    assert result["call_id"] == "abc"
    assert result["duration_seconds"] == 60
    # Original keys still present
    assert result["Call_id"] == "abc"
    assert result["duration"] == 60
    assert result["transcript"] == "hello"


def test_empty_string_call_id_alias_skipped():
    payload = {"Call_id": "", "call_id": "real-id"}
    result = normalize_synthflow_payload(payload)
    assert result["call_id"] == "real-id"


def test_no_call_id_variants_returns_missing():
    payload = {"duration": 30, "transcript": "hi"}
    result = normalize_synthflow_payload(payload)
    assert not result.get("call_id")


# ─────────────────────────────────────────────────────────────────────────────
# 11–13: webhook route integration (mocked DB + RQ)
# ─────────────────────────────────────────────────────────────────────────────

def _make_test_client() -> TestClient:
    from app.main import create_app
    application = create_app()
    application.state.default_queue = None  # no Redis in tests
    return TestClient(application, raise_server_exceptions=False)


def test_webhook_accepts_Capital_Call_id():
    """Synthflow sends Call_id — must return 202, not 422."""
    client = _make_test_client()
    payload = {
        "Call_id": "sf-call-001",
        "call_status": "hangup_on_voicemail",
        "end_call_reason": "voicemail",
        "duration": 12,
        "transcript": "",
    }
    with patch("app.api.routes.webhooks.get_sync_session") as mock_sess:
        mock_job = MagicMock()
        mock_job.id = "job-001"
        mock_schedule = MagicMock(return_value=mock_job)
        mock_sess.return_value.__enter__ = lambda s, *a: MagicMock()
        mock_sess.return_value.__exit__ = MagicMock(return_value=False)

        with patch("app.api.routes.webhooks.schedule_job", mock_schedule):
            resp = client.post("/v1/webhooks/calls", json=payload)

    assert resp.status_code == 202
    data = resp.json()
    assert data["call_id"] == "sf-call-001"


def test_webhook_returns_422_when_no_call_id():
    client = _make_test_client()
    payload = {"duration": 30, "transcript": "hello"}
    resp = client.post("/v1/webhooks/calls", json=payload)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"]["code"] == "missing_call_id"


def test_webhook_422_logs_original_keys(caplog):
    import logging
    client = _make_test_client()
    payload = {"duration": 30, "some_unknown_field": "value"}
    with caplog.at_level(logging.WARNING, logger="app.api.routes.webhooks"):
        resp = client.post("/v1/webhooks/calls", json=payload)
    assert resp.status_code == 422
    # Warning log should mention the incoming keys for debugging
    assert any("keys=" in r.message for r in caplog.records)
