"""
Unit tests for app/services/ghl_oauth.py — GHL Marketplace OAuth token storage.

Covers:
  1.  store_tokens: creates a new row when none exists for the location
  2.  store_tokens: updates (upserts) an existing row in place
  3.  store_tokens: raises ValueError when locationId is missing from response
  4.  get_valid_access_token: returns None when no token row exists
  5.  get_valid_access_token: returns stored token when well within expiry
  6.  get_valid_access_token: refreshes and persists a new token when near expiry
  7.  get_valid_access_token: propagates GhlConversationsError when refresh fails
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.adapters.ghl_conversations import GhlConversationsError
from app.config.settings import Settings
from app.models import Base
from app.models.ghl_oauth_token import GhlOAuthToken
from app.services.ghl_oauth import get_valid_access_token, store_tokens


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        ghl_marketplace_client_id="cid",
        ghl_marketplace_client_secret="csecret",
        ghl_conversation_provider_id="pid",
        ghl_retry_max=1,
    )


def _token_response(**overrides) -> dict:
    defaults = dict(
        locationId="loc-1",
        access_token="access-1",
        refresh_token="refresh-1",
        expires_in=86399,
    )
    defaults.update(overrides)
    return defaults


# ─────────────────────────────────────────────────────────────────────────────
# store_tokens
# ─────────────────────────────────────────────────────────────────────────────

def test_store_tokens_creates_new_row(session):
    row = store_tokens(session, _token_response())
    session.flush()

    assert row.location_id == "loc-1"
    assert row.access_token == "access-1"
    assert row.refresh_token == "refresh-1"
    fetched = session.get(GhlOAuthToken, "loc-1")
    assert fetched is not None


def test_store_tokens_updates_existing_row(session):
    store_tokens(session, _token_response())
    session.flush()

    store_tokens(session, _token_response(access_token="access-2", refresh_token="refresh-2"))
    session.flush()

    row = session.get(GhlOAuthToken, "loc-1")
    assert row.access_token == "access-2"
    assert row.refresh_token == "refresh-2"
    # Still exactly one row for this location — upsert, not a duplicate insert.
    count = session.query(GhlOAuthToken).filter_by(location_id="loc-1").count()
    assert count == 1


def test_store_tokens_raises_without_location_id(session):
    with pytest.raises(ValueError, match="locationId"):
        store_tokens(session, {"access_token": "a", "refresh_token": "r", "expires_in": 100})


# ─────────────────────────────────────────────────────────────────────────────
# get_valid_access_token
# ─────────────────────────────────────────────────────────────────────────────

def test_get_valid_access_token_returns_none_when_no_row(session):
    result = get_valid_access_token(session, "unknown-location", _settings())
    assert result is None


def test_get_valid_access_token_returns_stored_token_when_fresh(session):
    row = GhlOAuthToken(
        location_id="loc-fresh",
        access_token="fresh-token",
        refresh_token="fresh-refresh",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(hours=20),
    )
    session.add(row)
    session.flush()

    mock_client = MagicMock()
    result = get_valid_access_token(session, "loc-fresh", _settings(), _client=mock_client)

    assert result == "fresh-token"
    mock_client.refresh_access_token.assert_not_called()


def test_get_valid_access_token_refreshes_when_near_expiry(session):
    row = GhlOAuthToken(
        location_id="loc-stale",
        access_token="stale-token",
        refresh_token="stale-refresh",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=30),  # within skew window
    )
    session.add(row)
    session.flush()

    mock_client = MagicMock()
    mock_client.refresh_access_token.return_value = _token_response(
        locationId="loc-stale", access_token="new-token", refresh_token="new-refresh"
    )

    result = get_valid_access_token(session, "loc-stale", _settings(), _client=mock_client)

    assert result == "new-token"
    mock_client.refresh_access_token.assert_called_once_with("stale-refresh")
    persisted = session.get(GhlOAuthToken, "loc-stale")
    assert persisted.access_token == "new-token"
    assert persisted.refresh_token == "new-refresh"


def test_get_valid_access_token_propagates_refresh_failure(session):
    row = GhlOAuthToken(
        location_id="loc-broken",
        access_token="old-token",
        refresh_token="revoked-refresh",
        expires_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),  # already expired
    )
    session.add(row)
    session.flush()

    mock_client = MagicMock()
    mock_client.refresh_access_token.side_effect = GhlConversationsError("refresh_token revoked")

    with pytest.raises(GhlConversationsError, match="revoked"):
        get_valid_access_token(session, "loc-broken", _settings(), _client=mock_client)
