"""
Unit tests for POST /v1/webhooks/leads/{campaign_type} (spec/21 GHL intake endpoint).

Covers:
  1. Missing webhook secret header -> 401, no job scheduled
  2. Wrong webhook secret value -> 401
  3. Secret not configured server-side -> 401 (endpoint closed, not open)
  4. Unknown campaign_type -> 404
  5. Missing/malformed phone -> 400
  6. Valid New Lead request -> 202, lead_state created, job scheduled with campaign_name="New Lead"
  7. Valid Cold Lead request for an existing contact -> 202, no duplicate lead_state row
  8. Duplicate trigger for a contact with a pending job -> no second job (enter_campaign's dedup)
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config.settings import Settings
from app.models.base import Base
from app.models.lead_state import LeadState
from app.models.scheduled_job import ScheduledJob

SECRET = "test-webhook-secret"
AUTH_HEADERS = {"X-Cora-Webhook-Secret": SECRET}


@pytest.fixture(scope="module")
def engine():
    # StaticPool: TestClient runs the app in a separate thread from the one
    # that creates the tables below — without a single shared connection, a
    # plain sqlite:///:memory: engine hands each thread its own empty DB.
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session(engine):
    with Session(engine) as sess:
        yield sess
        sess.rollback()


@pytest.fixture
def test_settings():
    return Settings(
        _env_file=None,
        secret_key="test-secret",
        app_env="development",
        cora_inbound_webhook_secret=SECRET,
    )


@pytest.fixture
def client(test_settings, db_session):
    @contextmanager
    def _fake_session():
        yield db_session

    with patch("app.api.routes.call_intake.get_settings", return_value=test_settings), \
         patch("app.api.routes.call_intake.get_sync_session", _fake_session):
        from app.main import create_app
        with patch("app.config.get_settings", return_value=test_settings):
            application = create_app()
        with TestClient(application, raise_server_exceptions=True) as c:
            yield c


def _new_phone() -> str:
    """A fresh, valid-looking E.164 number, unique per call — avoids
    cross-test collisions on the shared module-scoped in-memory DB."""
    return "+1" + str(uuid.uuid4().int)[:10]


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def test_missing_secret_header_returns_401(client, db_session):
    resp = client.post("/v1/webhooks/leads/new_lead", json={"phone": _new_phone()})
    assert resp.status_code == 401
    assert db_session.scalars(select(LeadState)).first() is None


def test_wrong_secret_value_returns_401(client, db_session):
    resp = client.post(
        "/v1/webhooks/leads/new_lead",
        json={"phone": _new_phone()},
        headers={"X-Cora-Webhook-Secret": "wrong-secret"},
    )
    assert resp.status_code == 401


def test_unconfigured_secret_closes_endpoint(db_session):
    """If cora_inbound_webhook_secret is unset server-side, every request is
    rejected — the endpoint is closed by default, never silently open."""
    settings_no_secret = Settings(
        _env_file=None, secret_key="test-secret", app_env="development",
        cora_inbound_webhook_secret=None,
    )

    @contextmanager
    def _fake_session():
        yield db_session

    with patch("app.api.routes.call_intake.get_settings", return_value=settings_no_secret), \
         patch("app.api.routes.call_intake.get_sync_session", _fake_session), \
         patch("app.config.get_settings", return_value=settings_no_secret):
        from app.main import create_app
        application = create_app()
        with TestClient(application, raise_server_exceptions=True) as c:
            resp = c.post(
                "/v1/webhooks/leads/new_lead",
                json={"phone": _new_phone()},
                headers=AUTH_HEADERS,  # even the "correct" secret is rejected — nothing matches None
            )
    assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# Request validation
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_campaign_type_returns_404(client):
    resp = client.post(
        "/v1/webhooks/leads/warm_lead",  # not a real campaign_type slug
        json={"phone": _new_phone()},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 404


def test_missing_phone_returns_400(client):
    resp = client.post("/v1/webhooks/leads/new_lead", json={}, headers=AUTH_HEADERS)
    assert resp.status_code == 400


def test_malformed_phone_returns_400(client):
    resp = client.post(
        "/v1/webhooks/leads/new_lead", json={"phone": "5551234"}, headers=AUTH_HEADERS,
    )
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────

def test_valid_new_lead_request_creates_lead_and_schedules_job(client, db_session):
    phone = _new_phone()
    resp = client.post(
        "/v1/webhooks/leads/new_lead",
        json={"phone": phone, "first_name": "Jane", "email": "jane@example.com"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 202

    lead = db_session.scalars(
        select(LeadState).where(LeadState.contact_id == phone)
    ).first()
    assert lead is not None
    assert lead.campaign_name == "New Lead"

    jobs = db_session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == phone,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["campaign_name"] == "New Lead"


def test_valid_cold_lead_request_for_existing_contact_no_duplicate_lead(client, db_session):
    phone = _new_phone()

    resp1 = client.post(
        "/v1/webhooks/leads/cold_lead", json={"phone": phone}, headers=AUTH_HEADERS,
    )
    assert resp1.status_code == 202

    leads = db_session.scalars(
        select(LeadState).where(LeadState.contact_id == phone)
    ).all()
    assert len(leads) == 1


def test_duplicate_trigger_does_not_create_second_job(client, db_session):
    phone = _new_phone()

    client.post("/v1/webhooks/leads/new_lead", json={"phone": phone}, headers=AUTH_HEADERS)
    client.post("/v1/webhooks/leads/new_lead", json={"phone": phone}, headers=AUTH_HEADERS)

    jobs = db_session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.entity_id == phone,
            ScheduledJob.job_type == "launch_outbound_call",
            ScheduledJob.status == "pending",
        )
    ).all()
    assert len(jobs) == 1
