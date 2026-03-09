"""
Phase 7 — Synthflow adapter tests.

All tests mock httpx.Client — no real Synthflow API calls.

Covers:
  1.  Client initializes with settings
  2.  validate_for_synthflow called before schedule_callback (ConfigError without key)
  3.  Auth header: Authorization Bearer
  4.  build_callback_payload includes model_id, phone
  5.  build_callback_payload includes scheduled_time when provided
  6.  build_callback_payload uses settings.synthflow_model_id when model_id=None
  7.  build_callback_payload omits scheduled_time when None
  8.  build_callback_payload includes metadata when provided
  9.  schedule_callback calls _post with correct payload
  10. schedule_callback: retry on 429
  11. schedule_callback: retry on 500
  12. schedule_callback: no retry on 404
  13. schedule_callback: raises SynthflowError after exhausting retries
  14. schedule_callback: retries on TimeoutException
  15. Context manager: close() called on __exit__
  16. SynthflowError has correct status_code attribute
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.synthflow import SynthflowClient, SynthflowError
from app.config.settings import ConfigError, Settings

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    defaults = dict(
        synthflow_api_key="sf-test-key",
        synthflow_model_id="model-id-001",
        synthflow_base_url="https://api.synthflow.ai/v2/calls",
        synthflow_timeout_seconds=5,
        synthflow_retry_max=2,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = b"x"
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _make_client(settings: Settings) -> tuple[SynthflowClient, MagicMock]:
    mock_http = MagicMock(spec=httpx.Client)
    client = SynthflowClient(settings=settings, _http=mock_http)
    return client, mock_http


# ─────────────────────────────────────────────────────────────────────────────
# 1. Initialization
# ─────────────────────────────────────────────────────────────────────────────

def test_client_initializes():
    s = _settings()
    client, _ = _make_client(s)
    assert client.settings is s


# ─────────────────────────────────────────────────────────────────────────────
# 2. validate_for_synthflow called
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_callback_raises_without_api_key():
    s = _settings(synthflow_api_key=None)
    client, _ = _make_client(s)
    with pytest.raises(ConfigError, match="SYNTHFLOW_API_KEY"):
        client.schedule_callback(phone="+15551234567")


def test_schedule_callback_raises_without_model_id():
    s = _settings(synthflow_model_id=None)
    client, _ = _make_client(s)
    with pytest.raises(ConfigError, match="SYNTHFLOW_MODEL_ID"):
        client.schedule_callback(phone="+15551234567")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Auth header
# ─────────────────────────────────────────────────────────────────────────────

def test_headers_include_bearer_token():
    s = _settings()
    client, _ = _make_client(s)
    headers = client._headers()
    assert headers["Authorization"] == "Bearer sf-test-key"


def test_headers_include_content_type():
    s = _settings()
    client, _ = _make_client(s)
    assert client._headers()["Content-Type"] == "application/json"


# ─────────────────────────────────────────────────────────────────────────────
# 4-8. build_callback_payload
# ─────────────────────────────────────────────────────────────────────────────

def test_payload_includes_model_id_and_phone():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_callback_payload(phone="+15551234567")
    assert payload["model_id"] == "model-id-001"
    assert payload["phone"] == "+15551234567"


def test_payload_includes_scheduled_time():
    s = _settings()
    client, _ = _make_client(s)
    dt = datetime(2024, 6, 15, 14, 0, 0, tzinfo=timezone.utc)
    payload = client.build_callback_payload(phone="+15551234567", scheduled_time=dt)
    assert "scheduled_time" in payload
    assert "2024-06-15T14:00:00" in payload["scheduled_time"]


def test_payload_uses_settings_model_id_when_not_provided():
    s = _settings(synthflow_model_id="settings-model-999")
    client, _ = _make_client(s)
    payload = client.build_callback_payload(phone="+15551234567", model_id=None)
    assert payload["model_id"] == "settings-model-999"


def test_payload_uses_explicit_model_id_when_provided():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_callback_payload(phone="+15551234567", model_id="override-model")
    assert payload["model_id"] == "override-model"


def test_payload_omits_scheduled_time_when_none():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_callback_payload(phone="+15551234567", scheduled_time=None)
    assert "scheduled_time" not in payload


def test_payload_includes_metadata():
    s = _settings()
    client, _ = _make_client(s)
    meta = {"contact_id": "c-1", "tier": "0"}
    payload = client.build_callback_payload(phone="+15551234567", metadata=meta)
    assert payload["metadata"] == meta


def test_payload_omits_metadata_when_none():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_callback_payload(phone="+15551234567")
    assert "metadata" not in payload


# ─────────────────────────────────────────────────────────────────────────────
# 9. schedule_callback posts correct payload
# ─────────────────────────────────────────────────────────────────────────────

def test_schedule_callback_posts_to_base_url():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.post.return_value = _mock_response(200, {"id": "call-001"})

    result = client.schedule_callback(phone="+15551234567", _retry_delay=0)

    mock_http.post.assert_called_once()
    call_args = mock_http.post.call_args
    assert "api.synthflow.ai" in call_args[0][0]
    assert result == {"id": "call-001"}


def test_schedule_callback_includes_phone_in_payload():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.post.return_value = _mock_response(200, {})

    client.schedule_callback(phone="+15559876543", _retry_delay=0)

    call_kwargs = mock_http.post.call_args.kwargs
    assert call_kwargs["json"]["phone"] == "+15559876543"


# ─────────────────────────────────────────────────────────────────────────────
# 10-14. Retry logic
# ─────────────────────────────────────────────────────────────────────────────

def test_retries_on_429():
    from unittest.mock import patch

    s = _settings(synthflow_retry_max=2)
    client, mock_http = _make_client(s)

    first = _mock_response(429)
    first.raise_for_status = MagicMock()
    second = _mock_response(200, {"id": "call-ok"})
    mock_http.post.side_effect = [first, second]

    with patch("time.sleep"):
        result = client._post({}, _retry_delay=0)

    assert mock_http.post.call_count == 2
    assert result == {"id": "call-ok"}


def test_retries_on_500():
    from unittest.mock import patch

    s = _settings(synthflow_retry_max=2)
    client, mock_http = _make_client(s)

    first = _mock_response(500)
    first.raise_for_status = MagicMock()
    second = _mock_response(200, {"ok": True})
    mock_http.post.side_effect = [first, second]

    with patch("time.sleep"):
        result = client._post({}, _retry_delay=0)

    assert result == {"ok": True}


def test_does_not_retry_on_404():
    s = _settings(synthflow_retry_max=3)
    client, mock_http = _make_client(s)
    mock_http.post.return_value = _mock_response(404)

    with pytest.raises(SynthflowError, match="404"):
        client._post({}, _retry_delay=0)

    assert mock_http.post.call_count == 1


def test_raises_after_exhausting_retries():
    from unittest.mock import patch

    s = _settings(synthflow_retry_max=2)
    client, mock_http = _make_client(s)
    mock_http.post.return_value = _mock_response(503)

    with patch("time.sleep"):
        with pytest.raises(SynthflowError):
            client._post({}, _retry_delay=0)

    assert mock_http.post.call_count == 3


def test_retries_on_timeout():
    from unittest.mock import patch

    s = _settings(synthflow_retry_max=2)
    client, mock_http = _make_client(s)
    mock_http.post.side_effect = [
        httpx.TimeoutException("timed out"),
        _mock_response(200, {"id": "call-ok"}),
    ]

    with patch("time.sleep"):
        result = client._post({}, _retry_delay=0)

    assert result == {"id": "call-ok"}


# ─────────────────────────────────────────────────────────────────────────────
# 15. Context manager
# ─────────────────────────────────────────────────────────────────────────────

def test_context_manager_calls_close():
    s = _settings()
    mock_http = MagicMock(spec=httpx.Client)
    client = SynthflowClient(settings=s, _http=mock_http)
    with client:
        pass
    mock_http.close.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 16. SynthflowError attributes
# ─────────────────────────────────────────────────────────────────────────────

def test_synthflow_error_status_code():
    err = SynthflowError("test error", status_code=429)
    assert err.status_code == 429
    assert "test error" in str(err)


def test_synthflow_error_optional_status_code():
    err = SynthflowError("timeout error")
    assert err.status_code is None
