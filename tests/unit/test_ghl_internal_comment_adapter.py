"""
Unit tests for app/adapters/ghl_internal_comment.py.

All tests mock httpx.Client — no real GHL API calls are made.

Covers:
  1.  Shadow mode: no HTTP call made, shadow dict returned
  2.  Live write: correct URL, payload shape (contactId/type/message), headers
  3.  Message includes transcript and, when present, the recording URL
  4.  Message omits the recording section entirely when recording_url is None
  5.  Retry: retries on HTTP 429, succeeds on second attempt
  6.  Retry: raises GhlInternalCommentError after exhausting retries
  7.  validate_for_ghl_internal_comment: raises when key missing
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.adapters.ghl_internal_comment import GhlInternalCommentClient, GhlInternalCommentError
from app.config.settings import ConfigError, Settings


def _settings(**overrides) -> Settings:
    defaults = dict(
        ghl_conversations_api_key="test-conversations-key",
        ghl_write_internal_comment=True,
        ghl_retry_max=2,
        ghl_timeout_seconds=5,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_response(status_code: int = 201, body: dict | None = None, text: str = "") -> MagicMock:
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


def _make_client(settings: Settings) -> tuple[GhlInternalCommentClient, MagicMock]:
    mock_http = MagicMock(spec=httpx.Client)
    client = GhlInternalCommentClient(settings=settings, _http=mock_http)
    return client, mock_http


def test_shadow_mode_makes_no_http_call():
    client, mock_http = _make_client(_settings(ghl_write_internal_comment=False))

    result = client.write_call_note(
        contact_id="contact-1", call_id="call-1", transcript="hello", recording_url="https://x/rec.wav",
    )

    mock_http.request.assert_not_called()
    assert result["shadow"] is True
    assert result["contact_id"] == "contact-1"


def test_live_write_correct_payload_and_headers():
    client, mock_http = _make_client(_settings())
    mock_http.request.return_value = _mock_response(
        201, {"conversationId": "conv-1", "messageId": "msg-1"}
    )

    result = client.write_call_note(
        contact_id="contact-1", call_id="call-1", transcript="bot: hi\nhuman: hey", recording_url="https://x/rec.wav",
    )

    assert result["messageId"] == "msg-1"
    args, kwargs = mock_http.request.call_args
    assert args[0] == "POST"
    assert args[1] == "/conversations/messages"
    assert kwargs["json"]["contactId"] == "contact-1"
    assert kwargs["json"]["type"] == "InternalComment"
    assert "bot: hi" in kwargs["json"]["message"]
    assert "Recording: https://x/rec.wav" in kwargs["json"]["message"]
    assert kwargs["headers"]["Authorization"] == "Bearer test-conversations-key"
    assert kwargs["headers"]["Version"] == "2021-07-28"


def test_message_omits_recording_section_when_url_missing():
    client, mock_http = _make_client(_settings())
    mock_http.request.return_value = _mock_response(201, {"messageId": "msg-1"})

    client.write_call_note(
        contact_id="contact-1", call_id="call-1", transcript="bot: hi", recording_url=None,
    )

    _, kwargs = mock_http.request.call_args
    assert "Recording:" not in kwargs["json"]["message"]


def test_retries_on_429_then_succeeds():
    client, mock_http = _make_client(_settings())
    mock_http.request.side_effect = [
        _mock_response(429),
        _mock_response(201, {"messageId": "msg-1"}),
    ]

    result = client.write_call_note(
        contact_id="contact-1", call_id="call-1", transcript="hi", recording_url=None,
    )

    assert result["messageId"] == "msg-1"
    assert mock_http.request.call_count == 2


def test_raises_after_exhausting_retries():
    client, mock_http = _make_client(_settings(ghl_retry_max=1))
    mock_http.request.return_value = _mock_response(500)

    with pytest.raises(GhlInternalCommentError):
        client.write_call_note(
            contact_id="contact-1", call_id="call-1", transcript="hi", recording_url=None,
        )


def test_validate_raises_when_key_missing():
    settings = _settings(ghl_conversations_api_key=None)
    with pytest.raises(ConfigError, match="GHL_CONVERSATIONS_API_KEY"):
        settings.validate_for_ghl_internal_comment()
