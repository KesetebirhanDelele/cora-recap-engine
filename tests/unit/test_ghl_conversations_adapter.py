"""
Unit tests for app/adapters/ghl_conversations.py — GHL Marketplace OAuth client.

All tests mock httpx.Client — no real GHL API calls are made.

Covers:
  1.  exchange_code_for_token — correct URL, method, form-encoded body, headers
  2.  exchange_code_for_token — includes redirect_uri when configured
  3.  exchange_code_for_token — omits redirect_uri when not configured
  4.  refresh_access_token — correct grant_type, refresh_token field
  4b. get_location_token — correct URL, form body, Bearer auth with company token
  5.  Retry: retries on HTTP 429, succeeds on second attempt
  6.  Retry: retries on HTTP 500
  7.  Retry: raises GhlConversationsError after exhausting retries
  8.  Retry: does NOT retry on 400 — raises immediately with response body in message
  9.  Retry: retries on TimeoutException
  10. Context manager: close() called on __exit__
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.adapters.ghl_conversations import GhlConversationsClient, GhlConversationsError
from app.config.settings import Settings


def _settings(**overrides) -> Settings:
    defaults = dict(
        ghl_marketplace_client_id="test-client-id",
        ghl_marketplace_client_secret="test-client-secret",
        ghl_conversation_provider_id="test-provider-id",
        ghl_retry_max=2,
        ghl_timeout_seconds=5,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_response(status_code: int = 200, body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = b"x"
    resp.json.return_value = body or {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _make_client(settings: Settings) -> tuple[GhlConversationsClient, MagicMock]:
    mock_http = MagicMock(spec=httpx.Client)
    client = GhlConversationsClient(settings=settings, _http=mock_http)
    return client, mock_http


# ─────────────────────────────────────────────────────────────────────────────
# Token exchange
# ─────────────────────────────────────────────────────────────────────────────

def test_exchange_code_for_token_request_shape():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(
        200, {"access_token": "at", "refresh_token": "rt", "expires_in": 86399, "locationId": "loc-1"}
    )

    result = client.exchange_code_for_token("auth-code-123")

    call_args = mock_http.request.call_args
    assert call_args.args == ("POST", "/oauth/token")
    data = call_args.kwargs["data"]
    assert data["client_id"] == "test-client-id"
    assert data["client_secret"] == "test-client-secret"
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "auth-code-123"
    assert data["user_type"] == "Location"
    assert call_args.kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert result["locationId"] == "loc-1"


def test_exchange_code_for_token_includes_redirect_uri_when_configured():
    s = _settings(ghl_oauth_redirect_uri="https://example.com/oauth/callback")
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {})

    client.exchange_code_for_token("code")

    data = mock_http.request.call_args.kwargs["data"]
    assert data["redirect_uri"] == "https://example.com/oauth/callback"


def test_exchange_code_for_token_omits_redirect_uri_when_not_configured():
    s = _settings(ghl_oauth_redirect_uri=None)
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {})

    client.exchange_code_for_token("code")

    data = mock_http.request.call_args.kwargs["data"]
    assert "redirect_uri" not in data


def test_refresh_access_token_request_shape():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(
        200, {"access_token": "at2", "refresh_token": "rt2", "expires_in": 86399, "locationId": "loc-1"}
    )

    result = client.refresh_access_token("old-refresh-token")

    data = mock_http.request.call_args.kwargs["data"]
    assert data["grant_type"] == "refresh_token"
    assert data["refresh_token"] == "old-refresh-token"
    assert data["user_type"] == "Location"
    assert result["access_token"] == "at2"


# ─────────────────────────────────────────────────────────────────────────────
# Company -> Location token exchange (spec/20 §7)
# ─────────────────────────────────────────────────────────────────────────────

def test_get_location_token_request_shape():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(
        201, {"access_token": "loc-at", "refresh_token": "loc-rt", "expires_in": 86400,
              "userType": "Location", "locationId": "loc-1"}
    )

    result = client.get_location_token("company-access-token", "company-1", "loc-1")

    call_args = mock_http.request.call_args
    assert call_args.args == ("POST", "/oauth/locationToken")
    assert call_args.kwargs["data"] == {"companyId": "company-1", "locationId": "loc-1"}
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer company-access-token"
    assert call_args.kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert result["userType"] == "Location"


# ─────────────────────────────────────────────────────────────────────────────
# Retry policy — mirrors GHLClient's contract (spec/16)
# ─────────────────────────────────────────────────────────────────────────────

def test_retries_on_429_succeeds_second_attempt():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    first = _mock_response(429)
    second = _mock_response(200, {"access_token": "at"})
    mock_http.request.side_effect = [first, second]

    with patch("time.sleep"):
        result = client._request("POST", "/oauth/token", _retry_delay=0)

    assert mock_http.request.call_count == 2
    assert result == {"access_token": "at"}


def test_retries_on_500():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    mock_http.request.side_effect = [_mock_response(500), _mock_response(200, {})]

    with patch("time.sleep"):
        client._request("POST", "/oauth/token", _retry_delay=0)

    assert mock_http.request.call_count == 2


def test_raises_after_exhausting_retries():
    s = _settings(ghl_retry_max=1)
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(500)

    with patch("time.sleep"):
        with pytest.raises(GhlConversationsError):
            client._request("POST", "/oauth/token", _retry_delay=0)


def test_does_not_retry_on_400_includes_body_in_message():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(400, text='{"message": "invalid code"}')

    with pytest.raises(GhlConversationsError, match="invalid code"):
        client._request("POST", "/oauth/token")

    assert mock_http.request.call_count == 1


def test_retries_on_timeout_exception():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)
    mock_http.request.side_effect = [
        httpx.TimeoutException("timed out"),
        _mock_response(200, {}),
    ]

    with patch("time.sleep"):
        client._request("POST", "/oauth/token", _retry_delay=0)

    assert mock_http.request.call_count == 2


def test_close_calls_underlying_http_close():
    s = _settings()
    client, mock_http = _make_client(s)
    with client:
        pass
    mock_http.close.assert_called_once()
