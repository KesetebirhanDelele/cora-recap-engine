"""
Phase 7 — tier policy and duplicate callback prevention tests.

DB interactions use SQLite in-memory.

Covers:
  1.  Cold Lead None → '0': correct delay (120 min), callback=True, terminal=False
  2.  Cold Lead '0' → '1': correct delay (2880 min)
  3.  Cold Lead '1' → '2': correct delay (2880 min)
  4.  Cold Lead '2' → '3': terminal=True, callback=False, delay=0
  5.  Cold Lead invalid tier raises ValueError
  6.  Cold Lead at terminal tier '3' raises ValueError
  7.  New Lead: unresolved delays raise ConfigError
  8.  New Lead: all delays set → correct policy returned
  9.  get_tier_policy dispatches to Cold Lead by name
  10. get_tier_policy dispatches to New Lead by name
  11. get_tier_policy unknown campaign raises ValueError
  12. has_pending_callback: returns True when pending callback exists
  13. has_pending_callback: returns True when claimed callback exists
  14. has_pending_callback: returns False when no callback exists
  15. has_pending_callback: returns False for completed/failed/cancelled callbacks
  16. TierTransitionPolicy is frozen (immutable)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.services.tier_policy import (
    TierTransitionPolicy,
    get_cold_lead_policy,
    get_tier_policy,
    has_pending_callback,
)

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


def _settings(**overrides):
    from app.config.settings import Settings
    return Settings(_env_file=None, **overrides)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# 1-6. Cold Lead policy
# ─────────────────────────────────────────────────────────────────────────────

def test_cold_lead_none_to_0_delay():
    s = _settings(cold_vm_tier_none_delay_minutes=120)
    policy = get_cold_lead_policy(None, settings=s)
    assert policy.delay_minutes == 120
    assert policy.next_tier == "0"
    assert policy.schedule_synthflow_callback is True
    assert policy.is_terminal is False


def test_cold_lead_0_to_1_delay():
    s = _settings(cold_vm_tier_0_delay_minutes=2880)
    policy = get_cold_lead_policy("0", settings=s)
    assert policy.delay_minutes == 2880
    assert policy.next_tier == "1"
    assert policy.schedule_synthflow_callback is True
    assert policy.is_terminal is False


def test_cold_lead_1_to_2_delay():
    s = _settings(cold_vm_tier_1_delay_minutes=2880)
    policy = get_cold_lead_policy("1", settings=s)
    assert policy.delay_minutes == 2880
    assert policy.next_tier == "2"
    assert policy.schedule_synthflow_callback is True
    assert policy.is_terminal is False


def test_cold_lead_2_to_3_terminal():
    s = _settings()
    policy = get_cold_lead_policy("2", settings=s)
    assert policy.next_tier == "3"
    assert policy.is_terminal is True
    assert policy.schedule_synthflow_callback is False
    assert policy.delay_minutes == 0


def test_cold_lead_invalid_tier_raises():
    s = _settings()
    with pytest.raises(ValueError, match="Invalid Cold Lead tier"):
        get_cold_lead_policy("99", settings=s)


def test_cold_lead_terminal_tier_raises():
    s = _settings()
    with pytest.raises(ValueError, match="Invalid Cold Lead tier"):
        get_cold_lead_policy("3", settings=s)


def test_cold_lead_campaign_name_in_policy():
    s = _settings()
    policy = get_cold_lead_policy(None, settings=s)
    assert policy.campaign_name == "Cold Lead"


def test_cold_lead_policy_current_tier_preserved():
    s = _settings()
    policy = get_cold_lead_policy("0", settings=s)
    assert policy.current_tier == "0"


# ─────────────────────────────────────────────────────────────────────────────
# 7-8. New Lead policy
# ─────────────────────────────────────────────────────────────────────────────

def test_new_lead_unresolved_delays_raise_config_error():
    from app.config.settings import ConfigError

    s = _settings()  # NEW_VM_TIER delays all None
    from app.services.tier_policy import get_new_lead_policy

    with pytest.raises(ConfigError, match="NEW_VM_TIER"):
        get_new_lead_policy(None, settings=s)


def test_new_lead_all_delays_set_returns_policy():
    s = _settings(
        new_vm_tier_none_delay_minutes=60,
        new_vm_tier_0_delay_minutes=1440,
        new_vm_tier_1_delay_minutes=1440,
        new_vm_tier_2_finalize=True,
    )
    from app.services.tier_policy import get_new_lead_policy

    policy = get_new_lead_policy(None, settings=s)
    assert policy.delay_minutes == 60
    assert policy.next_tier == "0"
    assert policy.campaign_name == "New Lead"


def test_new_lead_terminal_tier():
    s = _settings(
        new_vm_tier_none_delay_minutes=60,
        new_vm_tier_0_delay_minutes=1440,
        new_vm_tier_1_delay_minutes=1440,
        new_vm_tier_2_finalize=True,
    )
    from app.services.tier_policy import get_new_lead_policy

    policy = get_new_lead_policy("2", settings=s)
    assert policy.is_terminal is True
    assert policy.schedule_synthflow_callback is False


# ─────────────────────────────────────────────────────────────────────────────
# 9-11. get_tier_policy dispatch
# ─────────────────────────────────────────────────────────────────────────────

def test_dispatch_cold_lead_by_name():
    s = _settings()
    policy = get_tier_policy("Cold Lead", None, settings=s)
    assert policy.campaign_name == "Cold Lead"


def test_dispatch_cold_lead_case_insensitive():
    s = _settings()
    policy = get_tier_policy("cold lead", None, settings=s)
    assert policy.campaign_name == "Cold Lead"


def test_dispatch_new_lead_raises_without_delays():
    from app.config.settings import ConfigError

    s = _settings()
    with pytest.raises(ConfigError):
        get_tier_policy("New Lead", None, settings=s)


def test_dispatch_unknown_campaign_raises():
    s = _settings()
    with pytest.raises(ValueError, match="Unknown campaign type"):
        get_tier_policy("Unknown Campaign", None, settings=s)


def test_dispatch_empty_campaign_raises():
    s = _settings()
    with pytest.raises(ValueError, match="Unknown campaign type"):
        get_tier_policy("", None, settings=s)


# ─────────────────────────────────────────────────────────────────────────────
# 12-15. has_pending_callback
# ─────────────────────────────────────────────────────────────────────────────

def _make_callback_job(session, entity_id, status="pending") -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="synthflow_callback",
        entity_type="lead",
        entity_id=entity_id,
        run_at=_now(),
        status=status,
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(job)
    session.flush()
    return job


def test_has_pending_callback_true_for_pending(session):
    entity_id = str(uuid.uuid4())
    _make_callback_job(session, entity_id, status="pending")
    assert has_pending_callback(session, entity_id) is True


def test_has_pending_callback_true_for_claimed(session):
    entity_id = str(uuid.uuid4())
    _make_callback_job(session, entity_id, status="claimed")
    assert has_pending_callback(session, entity_id) is True


def test_has_pending_callback_true_for_running(session):
    entity_id = str(uuid.uuid4())
    _make_callback_job(session, entity_id, status="running")
    assert has_pending_callback(session, entity_id) is True


def test_has_pending_callback_false_when_no_job(session):
    entity_id = str(uuid.uuid4())
    assert has_pending_callback(session, entity_id) is False


@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
def test_has_pending_callback_false_for_terminal_status(session, terminal_status):
    entity_id = str(uuid.uuid4())
    _make_callback_job(session, entity_id, status=terminal_status)
    assert has_pending_callback(session, entity_id) is False


def test_has_pending_callback_false_for_different_entity(session):
    entity_a = str(uuid.uuid4())
    entity_b = str(uuid.uuid4())
    _make_callback_job(session, entity_a, status="pending")
    assert has_pending_callback(session, entity_b) is False


def test_has_pending_callback_false_for_non_callback_job(session):
    """A pending job of a different type does not count as a callback."""
    entity_id = str(uuid.uuid4())
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="process_call_event",  # not synthflow_callback
        entity_type="lead",
        entity_id=entity_id,
        run_at=_now(),
        status="pending",
        version=0,
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(job)
    session.flush()
    assert has_pending_callback(session, entity_id) is False


# ─────────────────────────────────────────────────────────────────────────────
# 16. TierTransitionPolicy is frozen
# ─────────────────────────────────────────────────────────────────────────────

def test_tier_transition_policy_is_frozen():
    policy = TierTransitionPolicy(
        delay_minutes=120,
        schedule_synthflow_callback=True,
        is_terminal=False,
        campaign_name="Cold Lead",
        current_tier=None,
        next_tier="0",
    )
    with pytest.raises((AttributeError, TypeError)):
        policy.delay_minutes = 999  # type: ignore[misc]
