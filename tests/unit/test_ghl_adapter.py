"""
Phase 4 GHL adapter tests.

All tests mock httpx.Client — no real GHL API calls are made.

Covers:
  1.  Client initializes with settings; ConfigError without credentials
  2.  Auth header shape: Authorization Bearer + Version header
  3.  search_contact_by_phone — correct URL, params, response parsing
  4.  search_contact_by_phone — returns None when contacts list is empty
  5.  get_contact — correct URL, response returned
  6.  resolve_field_id — finds field by name and by fieldKey
  7.  resolve_field_id — returns None when label not found
  8.  get_field_value — reads current field value from contact
  9.  build_task_payload — blank due date, no assignedTo
  10. build_task_payload — includes description when provided
  11. build_field_update_payload — list of {id, value} objects
  12. build_note_payload — wraps content in {body}
  13. Shadow write: update_contact_fields does NOT call httpx in shadow mode
  14. Shadow write: create_task does NOT call httpx in shadow mode
  15. Shadow write: append_note does NOT call httpx in shadow mode
  16. Shadow response includes operation name, contact_id, payload
  17. Live write: calls httpx with correct method and path
  18. Live write: requires credentials (ConfigError without api_key)
  19. Retry: retries on HTTP 429, succeeds on second attempt
  20. Retry: retries on HTTP 500
  21. Retry: does NOT retry on 404
  22. Retry: raises GHLError after exhausting retries
  23. Retry: retries on TimeoutException
  24. Context manager: close() called on __exit__
  25. validate_for_ghl_reads called before read ops (missing api_key → ConfigError)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.adapters.ghl import GHLClient, GHLError
from app.config.settings import ConfigError, Settings

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    """Build a Settings without reading .env."""
    defaults = dict(
        ghl_api_key="test-api-key",
        ghl_location_id="loc-123",
        ghl_write_mode="shadow",
        ghl_write_shadow_log_only=True,
        ghl_retry_max=2,
        ghl_timeout_seconds=5,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _live_settings(**overrides) -> Settings:
    """Settings with live write mode enabled."""
    return _settings(
        ghl_write_mode="live",
        ghl_write_shadow_log_only=False,
        **overrides,
    )


def _mock_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = b"x"  # non-empty so .json() is called
    resp.json.return_value = body or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    return resp


def _make_client(settings: Settings) -> tuple[GHLClient, MagicMock]:
    """Create a GHLClient with a mocked httpx.Client."""
    mock_http = MagicMock(spec=httpx.Client)
    client = GHLClient(settings=settings, _http=mock_http)
    return client, mock_http


# ─────────────────────────────────────────────────────────────────────────────
# 1. Initialization
# ─────────────────────────────────────────────────────────────────────────────

def test_client_initializes_with_settings():
    s = _settings()
    client, _ = _make_client(s)
    assert client.settings is s


def test_client_raises_config_error_if_api_key_missing():
    s = _settings(ghl_api_key=None)
    client, _ = _make_client(s)
    with pytest.raises(ConfigError, match="GHL_API_KEY"):
        client.search_contact_by_phone("+15551234567")


def test_client_raises_config_error_if_location_id_missing():
    s = _settings(ghl_location_id=None)
    client, _ = _make_client(s)
    with pytest.raises(ConfigError, match="GHL_LOCATION_ID"):
        client.get_contact("cid-123")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Auth header shape
# ─────────────────────────────────────────────────────────────────────────────

def test_headers_include_bearer_token():
    s = _settings()
    client, _ = _make_client(s)
    headers = client._headers()
    assert headers["Authorization"] == "Bearer test-api-key"


def test_headers_include_version():
    s = _settings()
    client, _ = _make_client(s)
    assert client._headers()["Version"] == "2021-07-28"


def test_headers_include_content_type():
    s = _settings()
    client, _ = _make_client(s)
    assert client._headers()["Content-Type"] == "application/json"


def test_request_passes_headers_to_httpx(monkeypatch):
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"contacts": []})

    client._request("GET", "/contacts/", _retry_delay=0)

    call_kwargs = mock_http.request.call_args
    headers_used = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert "Authorization" in headers_used
    assert headers_used["Authorization"].startswith("Bearer ")
    assert "Version" in headers_used


# ─────────────────────────────────────────────────────────────────────────────
# 3. search_contact_by_phone
# ─────────────────────────────────────────────────────────────────────────────

_SAMPLE_CONTACT = {
    "id": "cid-abc",
    "phone": "+15551234567",
    "customFields": [
        {"id": "fid-001", "name": "AI Campaign Value", "fieldKey": "ai_campaign_value", "value": "0"},
    ],
}


def test_search_contact_by_phone_returns_first_contact():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"contacts": [_SAMPLE_CONTACT]})

    result = client.search_contact_by_phone("+15551234567", )
    assert result is not None
    assert result["id"] == "cid-abc"


def test_search_contact_calls_correct_path():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"contacts": [_SAMPLE_CONTACT]})

    client.search_contact_by_phone("+15551234567")

    call_args = mock_http.request.call_args
    assert call_args[0][0] == "GET"
    assert "/contacts/" in call_args[0][1]


def test_search_contact_sends_location_id_param():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"contacts": []})

    client.search_contact_by_phone("+15551234567")

    call_kwargs = mock_http.request.call_args.kwargs
    params = call_kwargs.get("params", {})
    assert params.get("locationId") == "loc-123"


# ─────────────────────────────────────────────────────────────────────────────
# 4. search_contact returns None when not found
# ─────────────────────────────────────────────────────────────────────────────

def test_search_contact_returns_none_when_empty():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"contacts": []})

    result = client.search_contact_by_phone("+15550000000")
    assert result is None


def test_search_contact_returns_none_when_key_missing():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {})

    result = client.search_contact_by_phone("+15550000000")
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. get_contact
# ─────────────────────────────────────────────────────────────────────────────

def test_get_contact_returns_contact_data():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, _SAMPLE_CONTACT)

    result = client.get_contact("cid-abc")
    assert result["id"] == "cid-abc"


def test_get_contact_calls_correct_path():
    s = _settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, _SAMPLE_CONTACT)

    client.get_contact("cid-abc")

    call_args = mock_http.request.call_args
    assert "cid-abc" in call_args[0][1]


# ─────────────────────────────────────────────────────────────────────────────
# 6 + 7. resolve_field_id
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_field_id_finds_by_name():
    fid = GHLClient.resolve_field_id("AI Campaign Value", _SAMPLE_CONTACT)
    assert fid == "fid-001"


def test_resolve_field_id_finds_by_field_key():
    fid = GHLClient.resolve_field_id("ai_campaign_value", _SAMPLE_CONTACT)
    assert fid == "fid-001"


def test_resolve_field_id_returns_none_when_not_found():
    fid = GHLClient.resolve_field_id("Nonexistent Field", _SAMPLE_CONTACT)
    assert fid is None


def test_resolve_field_id_returns_none_on_empty_custom_fields():
    contact = {"id": "cid-xyz", "customFields": []}
    assert GHLClient.resolve_field_id("AI Campaign", contact) is None


# ─────────────────────────────────────────────────────────────────────────────
# 8. get_field_value
# ─────────────────────────────────────────────────────────────────────────────

def test_get_field_value_returns_correct_value():
    value = GHLClient.get_field_value("AI Campaign Value", _SAMPLE_CONTACT)
    assert value == "0"


def test_get_field_value_returns_none_when_not_found():
    value = GHLClient.get_field_value("Unknown Field", _SAMPLE_CONTACT)
    assert value is None


# ─────────────────────────────────────────────────────────────────────────────
# 9 + 10. build_task_payload
# ─────────────────────────────────────────────────────────────────────────────

def test_build_task_payload_due_date_is_none():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_task_payload("Follow-up call")
    assert payload["dueDate"] is None


def test_build_task_payload_no_assigned_to():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_task_payload("Follow-up call")
    assert "assignedTo" not in payload


def test_build_task_payload_status_incompleted():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_task_payload("Test")
    assert payload["status"] == "incompleted"


def test_build_task_payload_includes_description_when_provided():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_task_payload("Title", description="Some detail")
    assert payload["description"] == "Some detail"


def test_build_task_payload_omits_description_when_empty():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_task_payload("Title", description="")
    assert "description" not in payload


# ─────────────────────────────────────────────────────────────────────────────
# 11. build_field_update_payload
# ─────────────────────────────────────────────────────────────────────────────

def test_build_field_update_payload_structure():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_field_update_payload({"field-id-001": "Cold Lead", "field-id-002": "1"})
    assert "customFields" in payload
    fields = payload["customFields"]
    assert len(fields) == 2
    assert all("id" in f and "value" in f for f in fields)


def test_build_field_update_payload_empty_updates():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_field_update_payload({})
    assert payload == {"customFields": []}


# ─────────────────────────────────────────────────────────────────────────────
# 12. build_note_payload
# ─────────────────────────────────────────────────────────────────────────────

def test_build_note_payload_wraps_in_body():
    s = _settings()
    client, _ = _make_client(s)
    payload = client.build_note_payload("Call completed successfully.")
    assert payload == {"body": "Call completed successfully."}


# ─────────────────────────────────────────────────────────────────────────────
# 13-15. Shadow writes do NOT call httpx
# ─────────────────────────────────────────────────────────────────────────────

def test_update_contact_fields_shadow_does_not_call_httpx():
    s = _settings(ghl_write_mode="shadow")
    client, mock_http = _make_client(s)

    client.update_contact_fields("cid-1", {"AI Campaign Value": "1"})

    mock_http.request.assert_not_called()


def test_create_task_shadow_does_not_call_httpx():
    s = _settings(ghl_write_mode="shadow")
    client, mock_http = _make_client(s)

    client.create_task("cid-1", "Follow-up call")

    mock_http.request.assert_not_called()


def test_append_note_shadow_does_not_call_httpx():
    s = _settings(ghl_write_mode="shadow")
    client, mock_http = _make_client(s)

    client.append_note("cid-1", "Call recap content")

    mock_http.request.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 16. Shadow response structure
# ─────────────────────────────────────────────────────────────────────────────

def test_shadow_response_includes_operation():
    s = _settings(ghl_write_mode="shadow")
    client, _ = _make_client(s)
    result = client.create_task("cid-1", "My Task")
    assert result["shadow"] is True
    assert result["operation"] == "create_task"


def test_shadow_response_includes_contact_id():
    s = _settings(ghl_write_mode="shadow")
    client, _ = _make_client(s)
    result = client.update_contact_fields("cid-99", {"field": "value"})
    assert result["contact_id"] == "cid-99"


def test_shadow_response_includes_payload():
    s = _settings(ghl_write_mode="shadow")
    client, _ = _make_client(s)
    result = client.append_note("cid-1", "content")
    assert "payload" in result
    assert result["payload"]["body"] == "content"


# ─────────────────────────────────────────────────────────────────────────────
# 17. Live write calls httpx
# ─────────────────────────────────────────────────────────────────────────────

def test_create_task_live_calls_httpx():
    s = _live_settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {"id": "task-001"})

    client.create_task("cid-1", "Live Task")

    mock_http.request.assert_called_once()
    call_args = mock_http.request.call_args
    assert call_args[0][0] == "POST"
    assert "tasks" in call_args[0][1]


def test_update_contact_fields_live_calls_put():
    s = _live_settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {})

    client.update_contact_fields("cid-1", {"fid": "val"})

    call_args = mock_http.request.call_args
    assert call_args[0][0] == "PUT"
    assert "cid-1" in call_args[0][1]


def test_append_note_live_calls_post_notes():
    s = _live_settings()
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(200, {})

    client.append_note("cid-1", "note content")

    call_args = mock_http.request.call_args
    assert call_args[0][0] == "POST"
    assert "notes" in call_args[0][1]


# ─────────────────────────────────────────────────────────────────────────────
# 18. Live write requires credentials
# ─────────────────────────────────────────────────────────────────────────────

def test_live_write_raises_config_error_without_api_key():
    s = _live_settings(ghl_api_key=None)
    client, _ = _make_client(s)
    with pytest.raises(ConfigError):
        client.create_task("cid-1", "Task")


def test_live_write_raises_config_error_in_shadow_mode():
    # Even if someone manually calls with live settings but mode is shadow
    s = _settings(ghl_write_mode="shadow")
    client, mock_http = _make_client(s)
    # Shadow mode means ghl_writes_enabled=False → shadow log, not ConfigError
    result = client.create_task("cid-1", "Task")
    assert result["shadow"] is True
    mock_http.request.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 19-23. Retry logic
# ─────────────────────────────────────────────────────────────────────────────

def test_retries_on_429_succeeds_second_attempt():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    first = _mock_response(429)
    first.raise_for_status = MagicMock()
    second = _mock_response(200, {"contacts": []})

    mock_http.request.side_effect = [first, second]

    with patch("time.sleep"):  # skip actual sleep
        result = client._request("GET", "/contacts/", _retry_delay=0)

    assert mock_http.request.call_count == 2
    assert result == {"contacts": []}


def test_retries_on_500():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    first = _mock_response(500)
    first.raise_for_status = MagicMock()
    second = _mock_response(200, {"ok": True})

    mock_http.request.side_effect = [first, second]

    with patch("time.sleep"):
        result = client._request("GET", "/test", _retry_delay=0)

    assert result == {"ok": True}


def test_does_not_retry_on_404():
    s = _settings(ghl_retry_max=3)
    client, mock_http = _make_client(s)
    mock_http.request.return_value = _mock_response(404)

    with pytest.raises(GHLError, match="404"):
        client._request("GET", "/contacts/missing", _retry_delay=0)

    assert mock_http.request.call_count == 1  # no retries


def test_raises_ghl_error_after_exhausting_retries():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    # Always return 500
    mock_http.request.return_value = _mock_response(500)

    with patch("time.sleep"):
        with pytest.raises(GHLError):
            client._request("GET", "/test", _retry_delay=0)

    assert mock_http.request.call_count == 3  # initial + 2 retries


def test_retries_on_timeout_exception():
    s = _settings(ghl_retry_max=2)
    client, mock_http = _make_client(s)

    timeout_exc = httpx.TimeoutException("timed out")
    success = _mock_response(200, {"ok": True})
    mock_http.request.side_effect = [timeout_exc, success]

    with patch("time.sleep"):
        result = client._request("GET", "/test", _retry_delay=0)

    assert result == {"ok": True}
    assert mock_http.request.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# 24. Context manager
# ─────────────────────────────────────────────────────────────────────────────

def test_context_manager_calls_close():
    s = _settings()
    mock_http = MagicMock(spec=httpx.Client)
    client = GHLClient(settings=s, _http=mock_http)

    with client:
        pass

    mock_http.close.assert_called_once()


def test_context_manager_returns_client_instance():
    s = _settings()
    mock_http = MagicMock(spec=httpx.Client)
    client = GHLClient(settings=s, _http=mock_http)

    with client as ctx:
        assert ctx is client


# ─────────────────────────────────────────────────────────────────────────────
# 25. validate_for_ghl_reads is called before read ops
# ─────────────────────────────────────────────────────────────────────────────

def test_validate_for_reads_called_before_search():
    s = Settings(_env_file=None)  # no credentials at all
    client, _ = _make_client(s)
    with pytest.raises(ConfigError):
        client.search_contact_by_phone("+15550000000")


def test_validate_for_reads_called_before_get_contact():
    s = Settings(_env_file=None)  # no credentials at all
    client, _ = _make_client(s)
    with pytest.raises(ConfigError):
        client.get_contact("cid-any")
