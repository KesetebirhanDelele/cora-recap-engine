"""
Unit tests for GET /oauth/callback — app/api/routes/ghl_oauth.py.

The route delegates entirely to app.services.ghl_oauth.complete_oauth_install()
(spec/20 §7 — that function owns the Company->Location token conversion), so
these tests mock at that boundary rather than re-testing the service logic
covered in test_ghl_oauth_service.py.

Covers:
  1.  Missing ?code param → 422 (FastAPI required-query validation)
  2.  Marketplace app not configured → config-error page, install never attempted
  3.  Successful install → success page shows the resulting locationId
  4.  Token exchange failure (GhlConversationsError) → connection-failed page
  5.  Install failure (ValueError — e.g. no target location configured) → connection-failed page
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.adapters.ghl_conversations import GhlConversationsError
from app.config.settings import Settings


def _configured_settings(**overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        secret_key="test-secret",
        ghl_marketplace_client_id="cid",
        ghl_marketplace_client_secret="csecret",
        ghl_conversation_provider_id="pid",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _unconfigured_settings() -> Settings:
    return Settings(
        _env_file=None,
        secret_key="test-secret",
        ghl_marketplace_client_id=None,
        ghl_marketplace_client_secret=None,
        ghl_conversation_provider_id=None,
    )


@contextmanager
def _dummy_sync_session():
    """The route only threads this through to complete_oauth_install, which
    is itself mocked in these tests — the session object's identity doesn't
    matter, it just needs to be a valid context manager."""
    yield SimpleNamespace()


def _build_app(settings: Settings):
    with patch("app.config.get_settings", return_value=settings):
        from app.main import create_app
        return create_app()


def test_missing_code_returns_422():
    application = _build_app(_configured_settings())
    with TestClient(application) as c:
        resp = c.get("/oauth/callback")
    assert resp.status_code == 422


def test_unconfigured_app_returns_config_error_page():
    settings = _unconfigured_settings()
    application = _build_app(settings)

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.complete_oauth_install") as mock_install:
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "abc"})

    assert resp.status_code == 200
    assert "Configuration error" in resp.text
    mock_install.assert_not_called()


def test_successful_exchange_shows_location():
    settings = _configured_settings()
    application = _build_app(settings)

    fake_token_row = SimpleNamespace(location_id="loc-route-test")

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.get_sync_session", _dummy_sync_session), \
         patch("app.api.routes.ghl_oauth.complete_oauth_install", return_value=fake_token_row) as mock_install:
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "valid-code"})

    assert resp.status_code == 200
    assert "loc-route-test" in resp.text
    assert "connected" in resp.text.lower()
    assert mock_install.call_args.args[1] == "valid-code"


def test_exchange_failure_returns_connection_failed_page():
    settings = _configured_settings()
    application = _build_app(settings)

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.get_sync_session", _dummy_sync_session), \
         patch("app.api.routes.ghl_oauth.complete_oauth_install",
               side_effect=GhlConversationsError("HTTP 400")):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "expired-code"})

    assert resp.status_code == 200
    assert "Connection failed" in resp.text


def test_install_value_error_returns_connection_failed_page():
    settings = _configured_settings()
    application = _build_app(settings)

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.get_sync_session", _dummy_sync_session), \
         patch("app.api.routes.ghl_oauth.complete_oauth_install",
               side_effect=ValueError("no target location configured")):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "code-no-location"})

    assert resp.status_code == 200
    assert "Connection failed" in resp.text
