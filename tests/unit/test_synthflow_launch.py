"""
Unit tests for Synthflow launch_new_lead_call and related settings.

Covers:
  1.  validate_for_synthflow_launch: raises ConfigError when URL missing
  2.  validate_for_synthflow_launch: passes when URL is set
  3.  launch_new_lead_call: POSTs to synthflow_launch_workflow_url (not base_url)
  4.  launch_new_lead_call: includes phone, lead_name, campaign_name in payload
  5.  launch_new_lead_call: includes metadata when provided
  6.  launch_new_lead_call: raises ConfigError when URL not configured
  7.  launch_new_lead_call: retries on 500 and succeeds
  8.  launch_new_lead_call: raises SynthflowError after exhausting retries
  9.  launch_new_lead_call: raises SynthflowError on 404 (non-retryable)
  10. launch_new_lead_call: returns empty dict on empty response body
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.synthflow import SynthflowClient, SynthflowError
from app.config.settings import ConfigError, Settings

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_LAUNCH_URL = "https://synthflow.example.com/webhooks/launch-abc"


def _settings(**overrides) -> Settings:
    defaults = dict(
        synthflow_api_key="sf-test-key",
        synthflow_model_id="model-id-001",
        synthflow_base_url="https://api.synthflow.ai/v2/calls",
        synthflow_launch_workflow_url=_LAUNCH_URL,
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


def _client(settings: Settings | None = None, mock_http: MagicMock | None = None) -> SynthflowClient:
    s = settings or _settings()
    http = mock_http or MagicMock(spec=httpx.Client)
    return SynthflowClient(settings=s, _http=http)


# ─────────────────────────────────────────────────────────────────────────────
# 1–2: validate_for_synthflow_launch
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_for_synthflow_launch_raises_when_url_missing():
    s = _settings(synthflow_launch_workflow_url=None)
    with pytest.raises(ConfigError, match="SYNTHFLOW_LAUNCH_WORKFLOW_URL"):
        s.validate_for_synthflow_launch()


def test_validate_for_synthflow_launch_passes_when_url_set():
    s = _settings(synthflow_launch_workflow_url=_LAUNCH_URL)
    s.validate_for_synthflow_launch()  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 3–10: launch_new_lead_call
# ─────────────────────────────────────────────────────────────────────────────

def test_launch_posts_to_launch_workflow_url_not_base_url():
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _mock_response(200, {"status": "ok"})
    client = _client(mock_http=http)

    client.launch_new_lead_call(
        phone="+15551234567",
        lead_name="Jane",
        _retry_delay=0.0,
    )

    url_used = http.post.call_args[0][0]
    assert url_used == _LAUNCH_URL
    assert "v2/calls" not in url_used


def test_launch_payload_contains_required_fields():
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _mock_response(200)
    client = _client(mock_http=http)

    client.launch_new_lead_call(
        phone="+15551234567",
        lead_name="Jane Doe",
        campaign_name="New_Lead",
        _retry_delay=0.0,
    )

    payload = http.post.call_args[1]["json"]
    assert payload["phone_number"] == "+15551234567"
    assert payload["lead_name"] == "Jane Doe"
    assert payload["campaign_name"] == "New_Lead"


def test_launch_includes_metadata_when_provided():
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _mock_response(200)
    client = _client(mock_http=http)

    client.launch_new_lead_call(
        phone="+15551234567",
        lead_name="Jane",
        metadata={"correlation_id": "abc-123"},
        _retry_delay=0.0,
    )

    payload = http.post.call_args[1]["json"]
    assert payload["metadata"]["correlation_id"] == "abc-123"


def test_launch_raises_config_error_when_url_not_configured():
    s = _settings(synthflow_launch_workflow_url=None)
    client = _client(settings=s)
    with pytest.raises(ConfigError, match="SYNTHFLOW_LAUNCH_WORKFLOW_URL"):
        client.launch_new_lead_call(phone="+15551234567", lead_name="Jane")


def test_launch_retries_on_500_and_succeeds():
    http = MagicMock(spec=httpx.Client)
    http.post.side_effect = [
        _mock_response(500),
        _mock_response(200, {"call_id": "sf-001"}),
    ]
    client = _client(mock_http=http)

    result = client.launch_new_lead_call(
        phone="+15551234567",
        lead_name="Jane",
        _retry_delay=0.0,
    )

    assert result == {"call_id": "sf-001"}
    assert http.post.call_count == 2


def test_launch_raises_after_exhausting_retries():
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _mock_response(500)
    s = _settings(synthflow_retry_max=2)
    client = _client(settings=s, mock_http=http)

    with pytest.raises(SynthflowError, match="failed after"):
        client.launch_new_lead_call(
            phone="+15551234567",
            lead_name="Jane",
            _retry_delay=0.0,
        )

    assert http.post.call_count == 3  # initial + 2 retries


def test_launch_raises_on_non_retryable_error():
    http = MagicMock(spec=httpx.Client)
    http.post.return_value = _mock_response(404)
    client = _client(mock_http=http)

    with pytest.raises((SynthflowError, httpx.HTTPStatusError)):
        client.launch_new_lead_call(
            phone="+15551234567",
            lead_name="Jane",
            _retry_delay=0.0,
        )

    assert http.post.call_count == 1


def test_launch_returns_empty_dict_on_empty_response():
    http = MagicMock(spec=httpx.Client)
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.content = b""
    resp.raise_for_status = MagicMock()
    http.post.return_value = resp
    client = _client(mock_http=http)

    result = client.launch_new_lead_call(
        phone="+15551234567",
        lead_name="Jane",
        _retry_delay=0.0,
    )
    assert result == {}
