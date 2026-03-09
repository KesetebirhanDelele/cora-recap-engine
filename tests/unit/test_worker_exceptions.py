"""
Phase 6 — worker exception record tests.

Uses SQLite in-memory — no real Postgres required.

Covers:
  1.  create_exception: creates a record with status='open'
  2.  create_exception: optional call_event_id is set when provided
  3.  create_exception: works without call_event_id (nullable FK)
  4.  create_exception: severity defaults to 'critical'
  5.  create_exception: context_json is stored
  6.  create_exception: entity_type and entity_id are stored
  7.  resolve_exception: open → resolved, returns True
  8.  resolve_exception: sets resolved_by and resolution_reason
  9.  resolve_exception: returns False on version conflict (concurrent action)
  10. resolve_exception: returns False for non-existent exception
  11. resolve_exception: returns False if already resolved
  12. ignore_exception: open → ignored, returns True
  13. ignore_exception: returns False on version conflict
  14. Concurrent operator actions: first wins, second loses
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.worker.exceptions import create_exception, ignore_exception, resolve_exception

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# 1-6. create_exception
# ─────────────────────────────────────────────────────────────────────────────

def test_create_exception_status_open(session):
    exc = create_exception(session, type="ghl_auth", context={})
    assert exc.status == "open"


def test_create_exception_with_call_event_id(session):
    exc = create_exception(
        session, type="identity_resolution", context={},
        call_event_id="fake-call-event-id",
    )
    assert exc.call_event_id == "fake-call-event-id"


def test_create_exception_without_call_event_id(session):
    exc = create_exception(session, type="ghl_auth", context={})
    assert exc.call_event_id is None


def test_create_exception_default_severity(session):
    exc = create_exception(session, type="openai_failed", context={})
    assert exc.severity == "critical"


def test_create_exception_custom_severity(session):
    exc = create_exception(session, type="call_pending", context={}, severity="warning")
    assert exc.severity == "warning"


def test_create_exception_context_stored(session):
    ctx = {"call_id": "call-123", "error": "timeout"}
    exc = create_exception(session, type="ghl_write_failed", context=ctx)
    assert exc.context_json == ctx


def test_create_exception_entity_fields(session):
    exc = create_exception(
        session, type="tier_invalid", context={},
        entity_type="lead", entity_id="contact-456",
    )
    assert exc.entity_type == "lead"
    assert exc.entity_id == "contact-456"


def test_create_exception_assigns_uuid(session):
    exc = create_exception(session, type="test", context={})
    assert exc.id and len(exc.id) == 36


# ─────────────────────────────────────────────────────────────────────────────
# 7-11. resolve_exception
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_exception_returns_true_on_success(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    result = resolve_exception(session, exc.id, resolved_by="ops-1", reason="fixed")
    assert result is True


def test_resolve_exception_sets_status_resolved(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    resolve_exception(session, exc.id, resolved_by="ops-1", reason="fixed")
    session.refresh(exc)
    assert exc.status == "resolved"


def test_resolve_exception_sets_resolved_by(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    resolve_exception(session, exc.id, resolved_by="ops-user-99", reason="ok")
    session.refresh(exc)
    assert exc.resolved_by == "ops-user-99"


def test_resolve_exception_sets_reason(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    resolve_exception(session, exc.id, resolved_by="ops", reason="duplicate confirmed")
    session.refresh(exc)
    assert exc.resolution_reason == "duplicate confirmed"


def test_resolve_exception_returns_false_for_nonexistent(session):
    result = resolve_exception(session, str(uuid.uuid4()), resolved_by="ops", reason="x")
    assert result is False


def test_resolve_exception_returns_false_if_already_resolved(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    resolve_exception(session, exc.id, resolved_by="ops-1", reason="first")
    result = resolve_exception(session, exc.id, resolved_by="ops-2", reason="second")
    assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# 9. resolve_exception: version conflict
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_exception_version_conflict(session):
    """
    Simulates two operators acting simultaneously.
    First resolve wins; second gets False.
    """
    exc = create_exception(session, type="conflict_test", context={})
    session.flush()

    first = resolve_exception(session, exc.id, resolved_by="ops-1", reason="first")
    assert first is True

    second = resolve_exception(session, exc.id, resolved_by="ops-2", reason="second")
    assert second is False

    session.refresh(exc)
    assert exc.resolved_by == "ops-1"


# ─────────────────────────────────────────────────────────────────────────────
# 12-13. ignore_exception
# ─────────────────────────────────────────────────────────────────────────────

def test_ignore_exception_returns_true(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    result = ignore_exception(session, exc.id, resolved_by="ops", reason="not relevant")
    assert result is True


def test_ignore_exception_sets_ignored_status(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    ignore_exception(session, exc.id, resolved_by="ops", reason="noise")
    session.refresh(exc)
    assert exc.status == "ignored"


def test_ignore_exception_version_conflict(session):
    exc = create_exception(session, type="test", context={})
    session.flush()
    ignore_exception(session, exc.id, resolved_by="ops-1", reason="first")
    second = ignore_exception(session, exc.id, resolved_by="ops-2", reason="second")
    assert second is False


# ─────────────────────────────────────────────────────────────────────────────
# 14. Concurrent operator actions: first wins
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_resolve_and_ignore_first_wins(session):
    """
    Operator A tries resolve; operator B tries ignore on same exception.
    Whichever executes first wins; the other gets False.
    """
    exc = create_exception(session, type="concurrent_test", context={})
    session.flush()

    r1 = resolve_exception(session, exc.id, resolved_by="op-a", reason="resolved")
    r2 = ignore_exception(session, exc.id, resolved_by="op-b", reason="ignored")

    # Only one succeeds
    assert r1 is True
    assert r2 is False
    session.refresh(exc)
    assert exc.status == "resolved"
    assert exc.resolved_by == "op-a"
