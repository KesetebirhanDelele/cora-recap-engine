"""
Phase 6 — worker retry policy tests.

Pure unit tests — no DB, no network.

Covers:
  1.  should_retry: returns True for retryable exceptions under limit
  2.  should_retry: returns False when max_attempts reached
  3.  should_retry: returns False for terminal exception types
  4.  should_retry: retryable_types=() retries any non-terminal exception
  5.  should_retry: terminal_types take priority over retryable_types
  6.  delay_seconds: doubles per attempt from base
  7.  delay_seconds: capped at 3600 seconds
  8.  attempts_remaining: correct count at each attempt
  9.  NO_RETRY_POLICY: never retries
  10. Pre-defined policies have correct max_attempts
"""
from __future__ import annotations

import pytest

from app.worker.retry import (
    DEFAULT_RETRY_POLICY,
    GHL_RETRY_POLICY,
    NO_RETRY_POLICY,
    OPENAI_RETRY_POLICY,
    RetryPolicy,
)


class _Transient(Exception):
    pass


class _Terminal(Exception):
    pass


class _Other(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 1. should_retry: retryable exceptions under limit
# ─────────────────────────────────────────────────────────────────────────────

def test_should_retry_true_on_first_attempt():
    policy = RetryPolicy(max_attempts=3, retryable_types=(_Transient,))
    assert policy.should_retry(0, _Transient()) is True


def test_should_retry_true_on_second_attempt():
    policy = RetryPolicy(max_attempts=3, retryable_types=(_Transient,))
    assert policy.should_retry(1, _Transient()) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. should_retry: returns False at max_attempts
# ─────────────────────────────────────────────────────────────────────────────

def test_should_retry_false_at_max_minus_one():
    policy = RetryPolicy(max_attempts=3)
    # attempt 2 = third try (0-indexed) = last allowed
    assert policy.should_retry(2, _Transient()) is False


def test_should_retry_false_beyond_max():
    policy = RetryPolicy(max_attempts=1)
    assert policy.should_retry(0, _Transient()) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. should_retry: terminal exception types
# ─────────────────────────────────────────────────────────────────────────────

def test_should_retry_false_for_terminal_exception():
    policy = RetryPolicy(
        max_attempts=5,
        terminal_types=(_Terminal,),
    )
    assert policy.should_retry(0, _Terminal()) is False


def test_should_retry_true_for_non_terminal():
    policy = RetryPolicy(
        max_attempts=5,
        terminal_types=(_Terminal,),
    )
    assert policy.should_retry(0, _Transient()) is True


# ─────────────────────────────────────────────────────────────────────────────
# 4. retryable_types=() retries any non-terminal exception
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_retryable_types_retries_any_exception():
    policy = RetryPolicy(max_attempts=3, retryable_types=())
    assert policy.should_retry(0, ValueError("anything")) is True
    assert policy.should_retry(0, RuntimeError("anything")) is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. terminal_types priority over retryable_types
# ─────────────────────────────────────────────────────────────────────────────

def test_terminal_takes_priority_over_retryable():
    policy = RetryPolicy(
        max_attempts=5,
        retryable_types=(_Terminal,),   # terminal is listed as retryable too
        terminal_types=(_Terminal,),    # but terminal wins
    )
    assert policy.should_retry(0, _Terminal()) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. delay_seconds: exponential backoff
# ─────────────────────────────────────────────────────────────────────────────

def test_delay_doubles_per_attempt():
    policy = RetryPolicy(max_attempts=5, base_delay_seconds=2.0)
    assert policy.delay_seconds(0) == 2.0
    assert policy.delay_seconds(1) == 4.0
    assert policy.delay_seconds(2) == 8.0
    assert policy.delay_seconds(3) == 16.0


def test_delay_starts_at_base():
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=5.0)
    assert policy.delay_seconds(0) == 5.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. delay_seconds: capped at 3600
# ─────────────────────────────────────────────────────────────────────────────

def test_delay_capped_at_3600():
    policy = RetryPolicy(max_attempts=100, base_delay_seconds=1.0)
    # 2^30 >> 3600, should be capped
    assert policy.delay_seconds(30) == 3600.0


# ─────────────────────────────────────────────────────────────────────────────
# 8. attempts_remaining
# ─────────────────────────────────────────────────────────────────────────────

def test_attempts_remaining_at_start():
    policy = RetryPolicy(max_attempts=3)
    assert policy.attempts_remaining(0) == 2


def test_attempts_remaining_one_left():
    policy = RetryPolicy(max_attempts=3)
    assert policy.attempts_remaining(1) == 1


def test_attempts_remaining_exhausted():
    policy = RetryPolicy(max_attempts=3)
    assert policy.attempts_remaining(2) == 0


def test_attempts_remaining_never_negative():
    policy = RetryPolicy(max_attempts=3)
    assert policy.attempts_remaining(99) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 9. NO_RETRY_POLICY
# ─────────────────────────────────────────────────────────────────────────────

def test_no_retry_policy_never_retries():
    assert NO_RETRY_POLICY.should_retry(0, RuntimeError("any")) is False


def test_no_retry_policy_max_attempts_is_one():
    assert NO_RETRY_POLICY.max_attempts == 1


# ─────────────────────────────────────────────────────────────────────────────
# 10. Pre-defined policies
# ─────────────────────────────────────────────────────────────────────────────

def test_default_policy_max_attempts():
    assert DEFAULT_RETRY_POLICY.max_attempts == 3


def test_ghl_policy_max_attempts():
    assert GHL_RETRY_POLICY.max_attempts == 3


def test_openai_policy_max_attempts():
    assert OPENAI_RETRY_POLICY.max_attempts == 3


def test_policies_are_frozen():
    with pytest.raises((AttributeError, TypeError)):
        DEFAULT_RETRY_POLICY.max_attempts = 99  # type: ignore[misc]
