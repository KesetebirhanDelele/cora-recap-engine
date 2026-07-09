"""
Unit tests for GET /oauth/callback — app/api/routes/ghl_oauth.py.

Covers:
  1.  Missing ?code param → 422 (FastAPI required-query validation)
  2.  Marketplace app not configured → config-error page, no HTTP call attempted
  3.  Successful exchange → token stored, success page shows locationId
  4.  Token exchange failure → connection-failed page, nothing stored
  5.  Response missing locationId → connection-failed page (store_tokens ValueError)
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.adapters.ghl_conversations import GhlConversationsError
from app.config.settings import Settings
from app.models import Base
from app.models.ghl_oauth_token import GhlOAuthToken


@pytest.fixture(scope="module")
def db_engine():
    # StaticPool (not the sqlite default SingletonThreadPool) — TestClient runs
    # the ASGI app through an anyio portal in a separate thread, and
    # SingletonThreadPool hands each thread its own :memory: DB (i.e. tables
    # created in the test thread would be invisible to the route handler).
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


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


def _fake_sync_session(session: Session):
    """A drop-in replacement for app.db.get_sync_session bound to a fixed Session."""
    @contextmanager
    def _fake():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
    return _fake


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

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "abc"})

    assert resp.status_code == 200
    assert "Configuration error" in resp.text


def test_successful_exchange_stores_token_and_shows_location(db_engine):
    settings = _configured_settings()
    application = _build_app(settings)
    session = Session(db_engine)

    mock_client = MagicMock()
    mock_client.exchange_code_for_token.return_value = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 86399,
        "locationId": "loc-route-test",
    }

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.GhlConversationsClient", return_value=mock_client), \
         patch("app.api.routes.ghl_oauth.get_sync_session", _fake_sync_session(session)):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "valid-code"})

    assert resp.status_code == 200
    assert "loc-route-test" in resp.text
    assert "connected" in resp.text.lower()

    stored = session.get(GhlOAuthToken, "loc-route-test")
    assert stored is not None
    assert stored.access_token == "at"
    session.close()


def test_exchange_failure_returns_connection_failed_page():
    settings = _configured_settings()
    application = _build_app(settings)

    mock_client = MagicMock()
    mock_client.exchange_code_for_token.side_effect = GhlConversationsError("HTTP 400")

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.GhlConversationsClient", return_value=mock_client):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "expired-code"})

    assert resp.status_code == 200
    assert "Connection failed" in resp.text


def test_missing_location_id_in_response_returns_connection_failed(db_engine):
    settings = _configured_settings()
    application = _build_app(settings)
    session = Session(db_engine)

    mock_client = MagicMock()
    mock_client.exchange_code_for_token.return_value = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 86399,
        # locationId deliberately omitted
    }

    with patch("app.api.routes.ghl_oauth.get_settings", return_value=settings), \
         patch("app.api.routes.ghl_oauth.GhlConversationsClient", return_value=mock_client), \
         patch("app.api.routes.ghl_oauth.get_sync_session", _fake_sync_session(session)):
        with TestClient(application) as c:
            resp = c.get("/oauth/callback", params={"code": "code-no-location"})

    assert resp.status_code == 200
    assert "Connection failed" in resp.text
    session.close()
