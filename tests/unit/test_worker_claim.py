"""
Phase 6 — worker claim/lease tests.

Uses SQLite in-memory — no real Postgres required.

Covers:
  1.  claim_job: succeeds on a pending job
  2.  claim_job: sets status='claimed', claimed_by, claimed_at, lease_expires_at
  3.  claim_job: increments version on claim
  4.  claim_job: returns None for already-claimed job (second attempt)
  5.  claim_job: returns None for completed job
  6.  claim_job: returns None for cancelled job
  7.  claim_job: returns None for non-existent job_id
  8.  Two sequential claims: second returns None (concurrent safety)
  9.  mark_running: advances claimed → running
  10. complete_job: advances running → completed
  11. fail_job: advances any → failed
  12. cancel_job: pending → cancelled, returns True
  13. cancel_job: completed job → returns False (no-op)
  14. cancel_job: non-existent job → returns False
  15. recover_expired_claims: resets expired claimed jobs to pending
  16. recover_expired_claims: does not reset non-expired claimed jobs
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, ScheduledJob
from app.worker.claim import (
    cancel_job,
    claim_job,
    complete_job,
    fail_job,
    mark_running,
    recover_expired_claims,
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


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_job(session: Session, status: str = "pending", **kwargs) -> ScheduledJob:
    job = ScheduledJob(
        id=str(uuid.uuid4()),
        job_type="test_job",
        entity_type="call",
        entity_id=str(uuid.uuid4()),
        run_at=_now(),
        status=status,
        version=0,
        created_at=_now(),
        updated_at=_now(),
        **kwargs,
    )
    session.add(job)
    session.flush()
    return job


# ─────────────────────────────────────────────────────────────────────────────
# 1. claim_job succeeds on pending job
# ─────────────────────────────────────────────────────────────────────────────

def test_claim_job_returns_job_on_success(session):
    job = _make_job(session)
    result = claim_job(session, job.id, worker_id="worker-01")
    assert result is not None
    assert result.id == job.id


# ─────────────────────────────────────────────────────────────────────────────
# 2. claim_job sets status fields
# ─────────────────────────────────────────────────────────────────────────────

def test_claim_job_sets_claimed_status(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="worker-01")
    session.refresh(job)
    assert job.status == "claimed"


def test_claim_job_sets_claimed_by(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="worker-99")
    session.refresh(job)
    assert job.claimed_by == "worker-99"


def test_claim_job_sets_claimed_at(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="w")
    session.refresh(job)
    assert job.claimed_at is not None


def test_claim_job_sets_lease_expires_at(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="w", lease_seconds=300)
    session.refresh(job)
    assert job.lease_expires_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# 3. version incremented on claim
# ─────────────────────────────────────────────────────────────────────────────

def test_claim_job_increments_version(session):
    job = _make_job(session)  # default version=0
    claim_job(session, job.id, worker_id="w")
    session.refresh(job)
    assert job.version == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4-7. claim_job returns None for non-claimable states
# ─────────────────────────────────────────────────────────────────────────────

def test_claim_job_returns_none_when_already_claimed(session):
    job = _make_job(session, status="claimed")
    result = claim_job(session, job.id, worker_id="w")
    assert result is None


def test_claim_job_returns_none_for_completed(session):
    job = _make_job(session, status="completed")
    assert claim_job(session, job.id, worker_id="w") is None


def test_claim_job_returns_none_for_cancelled(session):
    job = _make_job(session, status="cancelled")
    assert claim_job(session, job.id, worker_id="w") is None


def test_claim_job_returns_none_for_nonexistent(session):
    assert claim_job(session, str(uuid.uuid4()), worker_id="w") is None


# ─────────────────────────────────────────────────────────────────────────────
# 8. Two sequential claims — second returns None
# ─────────────────────────────────────────────────────────────────────────────

def test_two_sequential_claims_second_returns_none(session):
    """
    Simulates concurrent workers. After worker-01 claims, worker-02 gets None.
    The version mismatch prevents the second claim.
    """
    job = _make_job(session)

    first = claim_job(session, job.id, worker_id="worker-01")
    assert first is not None
    assert first.status == "claimed"

    # worker-02 tries to claim the same job
    second = claim_job(session, job.id, worker_id="worker-02")
    assert second is None

    session.refresh(job)
    assert job.claimed_by == "worker-01"


# ─────────────────────────────────────────────────────────────────────────────
# 9. mark_running
# ─────────────────────────────────────────────────────────────────────────────

def test_mark_running_advances_to_running(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="w")
    session.refresh(job)
    mark_running(session, job)
    session.refresh(job)
    assert job.status == "running"


# ─────────────────────────────────────────────────────────────────────────────
# 10. complete_job
# ─────────────────────────────────────────────────────────────────────────────

def test_complete_job_sets_completed(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="w")
    session.refresh(job)
    complete_job(session, job)
    session.refresh(job)
    assert job.status == "completed"


# ─────────────────────────────────────────────────────────────────────────────
# 11. fail_job
# ─────────────────────────────────────────────────────────────────────────────

def test_fail_job_sets_failed(session):
    job = _make_job(session)
    claim_job(session, job.id, worker_id="w")
    session.refresh(job)
    fail_job(session, job, reason="test error")
    session.refresh(job)
    assert job.status == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# 12-14. cancel_job
# ─────────────────────────────────────────────────────────────────────────────

def test_cancel_job_pending_returns_true(session):
    job = _make_job(session, status="pending")
    assert cancel_job(session, job.id) is True
    session.refresh(job)
    assert job.status == "cancelled"


def test_cancel_job_completed_returns_false(session):
    job = _make_job(session, status="completed")
    assert cancel_job(session, job.id) is False
    session.refresh(job)
    assert job.status == "completed"  # unchanged


def test_cancel_job_nonexistent_returns_false(session):
    assert cancel_job(session, str(uuid.uuid4())) is False


# ─────────────────────────────────────────────────────────────────────────────
# 15. recover_expired_claims: resets expired → pending
# ─────────────────────────────────────────────────────────────────────────────

def test_recover_expired_claims_resets_expired(session):
    past = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
    job = _make_job(
        session,
        status="claimed",
        claimed_by="crashed-worker",
        claimed_at=past,
        lease_expires_at=past,
    )
    # Force flush to DB
    session.flush()

    recovered = recover_expired_claims(session, worker_id="recovery-worker")
    assert job.id in recovered
    session.refresh(job)
    assert job.status == "pending"
    assert job.claimed_by is None


# ─────────────────────────────────────────────────────────────────────────────
# 16. recover_expired_claims: does not reset non-expired jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_recover_expired_claims_leaves_valid_claims(session):
    future = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
    job = _make_job(
        session,
        status="claimed",
        claimed_by="active-worker",
        claimed_at=_now(),
        lease_expires_at=future,
    )
    session.flush()

    recovered = recover_expired_claims(session, worker_id="recovery-worker")
    assert job.id not in recovered
    session.refresh(job)
    assert job.status == "claimed"
    assert job.claimed_by == "active-worker"
