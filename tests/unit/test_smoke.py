"""
Phase 1 smoke tests.

Verifies:
1. All application modules are importable without credentials.
2. The FastAPI app instance is created successfully.
3. All expected routes are registered.
4. Settings load with safe defaults in test environment.
5. Mode flags default to shadow/read-only in test environment.
6. Adapter stubs raise NotImplementedError (not silent failures).
7. Worker queue list is non-empty.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ── 1. Import smoke ───────────────────────────────────────────────────────────

def test_app_package_importable():
    import app
    assert app.__version__ == "0.1.0"


def test_config_importable():
    from app.config import Settings, get_settings
    assert Settings is not None
    assert get_settings is not None


def test_api_modules_importable():
    from app.api.routes import exceptions, webhooks
    assert webhooks.router is not None
    assert exceptions.router is not None


def test_worker_module_importable():
    from app.worker.main import get_queues
    assert get_queues is not None


def test_adapter_stubs_importable():
    from app.adapters.ghl import GHLClient
    from app.adapters.openai_client import OpenAIClient
    from app.adapters.sheets import GoogleSheetsClient
    from app.adapters.synthflow import SynthflowClient
    for cls in [GHLClient, SynthflowClient, OpenAIClient, GoogleSheetsClient]:
        assert cls is not None


# ── 2. App creation ───────────────────────────────────────────────────────────

def test_app_creates_successfully():
    from app.main import create_app
    created = create_app()
    assert created is not None


def test_app_healthcheck(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "true")
    from app.main import create_app
    app = create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "cora-recap-engine"


# ── 3. Route registration ─────────────────────────────────────────────────────

def test_webhook_route_registered():
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/v1/webhooks/calls" in routes


def test_exception_routes_registered():
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/v1/exceptions/{exception_id}/retry-now" in routes
    assert "/v1/exceptions/{exception_id}/retry-delay" in routes
    assert "/v1/exceptions/{exception_id}/cancel-future-jobs" in routes
    assert "/v1/exceptions/{exception_id}/force-finalize" in routes


# ── 4. Settings defaults ──────────────────────────────────────────────────────

def test_settings_load_without_credentials():
    from app.config.settings import Settings
    s = Settings()
    assert s.app_name == "cora-recap-engine"


def test_settings_external_credentials_optional():
    from app.config.settings import Settings
    s = Settings()
    # These must be Optional — no crash when absent
    assert s.ghl_api_key is None or isinstance(s.ghl_api_key, str)
    assert s.openai_api_key is None or isinstance(s.openai_api_key, str)
    assert s.synthflow_api_key is None or isinstance(s.synthflow_api_key, str)


# ── 5. Mode flags ─────────────────────────────────────────────────────────────

def test_shadow_mode_on_by_default(monkeypatch):
    monkeypatch.delenv("GHL_WRITE_MODE", raising=False)
    monkeypatch.delenv("SHADOW_MODE_ENABLED", raising=False)
    from app.config.settings import Settings
    s = Settings()
    # Default must be shadow-safe
    assert s.ghl_write_mode == "shadow"
    assert s.shadow_mode_enabled is True


def test_ghl_writes_not_enabled_in_shadow_mode(monkeypatch):
    monkeypatch.setenv("GHL_WRITE_MODE", "shadow")
    monkeypatch.setenv("GHL_WRITE_SHADOW_LOG_ONLY", "true")
    from app.config.settings import Settings
    s = Settings()
    assert s.ghl_writes_enabled is False


def test_ghl_writes_enabled_only_in_live_mode(monkeypatch):
    monkeypatch.setenv("GHL_WRITE_MODE", "live")
    monkeypatch.setenv("GHL_WRITE_SHADOW_LOG_ONLY", "false")
    from app.config.settings import Settings
    s = Settings()
    assert s.ghl_writes_enabled is True


def test_invalid_write_mode_raises(monkeypatch):
    monkeypatch.setenv("GHL_WRITE_MODE", "unknown")
    from app.config.settings import Settings
    with pytest.raises(Exception):
        Settings()


# ── 6. Adapter state ──────────────────────────────────────────────────────────

def test_ghl_client_importable_and_requires_credentials():
    # Phase 4: GHLClient is a real implementation.
    # Read ops raise ConfigError (not NotImplementedError) when credentials absent.
    from app.adapters.ghl import GHLClient
    from app.config.settings import ConfigError, Settings
    client = GHLClient(settings=Settings(_env_file=None))
    with pytest.raises(ConfigError):
        client.search_contact_by_phone("+10000000000")


def test_synthflow_client_requires_credentials():
    # Phase 7: SynthflowClient is a real implementation.
    # schedule_callback raises ConfigError when credentials are absent.
    from unittest.mock import MagicMock

    import httpx

    from app.adapters.synthflow import SynthflowClient
    from app.config.settings import ConfigError, Settings

    client = SynthflowClient(settings=Settings(_env_file=None), _http=MagicMock(spec=httpx.Client))
    with pytest.raises(ConfigError):
        client.schedule_callback(phone="+15550000000")


def test_openai_client_importable_and_requires_api_key():
    # Phase 5: OpenAIClient is a real implementation.
    # chat_completion raises ConfigError when OPENAI_API_KEY is absent.
    from unittest.mock import MagicMock

    from app.adapters.openai_client import OpenAIClient
    from app.config.settings import ConfigError, Settings
    client = OpenAIClient(settings=Settings(_env_file=None), _client=MagicMock())
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        client.chat_completion([{"role": "user", "content": "hi"}], model="gpt-4o-mini")


def test_sheets_stub_raises():
    from app.adapters.sheets import GoogleSheetsClient
    with pytest.raises(NotImplementedError):
        GoogleSheetsClient().mirror_google_sheet_rows({})


# ── 7. Worker queue list ──────────────────────────────────────────────────────

def test_worker_queues_non_empty():
    from app.worker.main import get_queues
    queues = get_queues()
    assert len(queues) >= 1
    assert "default" in queues


def test_worker_queues_contain_all_expected():
    from app.worker.main import get_queues
    queues = get_queues()
    for expected in ["default", "ai", "callbacks", "retries", "sheet_mirror"]:
        assert expected in queues, f"Queue '{expected}' not found in worker queues"


# ── 8. Webhook route behavior ─────────────────────────────────────────────────

def test_webhook_missing_call_id_returns_422():
    from app.main import app
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/v1/webhooks/calls", json={})
    assert resp.status_code == 422


def test_webhook_with_call_id_returns_202():
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    from app.main import create_app

    mock_job = MagicMock()
    mock_job.id = "job-test-001"

    @contextmanager
    def _no_db():
        yield MagicMock()

    with patch("app.api.routes.webhooks.get_sync_session", _no_db), \
         patch("app.api.routes.webhooks.schedule_job", return_value=mock_job):
        app = create_app()
        client = TestClient(app)
        resp = client.post("/v1/webhooks/calls", json={"call_id": "test-001"})

    assert resp.status_code == 202
    data = resp.json()
    assert data["call_id"] == "test-001"
    assert data["job_id"] == "job-test-001"
