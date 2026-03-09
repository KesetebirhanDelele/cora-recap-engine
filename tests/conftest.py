"""
Shared pytest fixtures.

Phase 1: minimal fixtures for smoke tests.
Later phases add: test DB session, mock GHL client,
mock OpenAI client, mock Redis, test event fixtures.
"""
from __future__ import annotations

import os

import pytest

# Override env vars before any settings are loaded so the app boots
# without real credentials in CI or local dev.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("GHL_WRITE_MODE", "shadow")
os.environ.setdefault("GHL_WRITE_SHADOW_LOG_ONLY", "true")
os.environ.setdefault("SHADOW_MODE_ENABLED", "true")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("WEBHOOK_SHARED_SECRET", "test-webhook-secret")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the lru_cache on get_settings between tests."""
    from app.config.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
