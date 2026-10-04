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


@pytest.fixture(autouse=True)
def _call_gate_allows_by_default(request):
    """The opt-out call gate (spec/39) reads GHL live. Existing call-job tests mock settings, so let the gate allow unless a
    test module opts in with `pytestmark = pytest.mark.real_call_gate` (tests/unit/test_call_gate.py)."""
    if request.node.get_closest_marker("real_call_gate"):
        yield
        return
    from unittest.mock import patch

    from app.services import call_gate

    with patch.object(call_gate, "check", return_value=call_gate.GateResult("allow")):
        yield
