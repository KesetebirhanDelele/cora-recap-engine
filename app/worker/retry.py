"""
Retry policy definitions for worker jobs.

RetryPolicy is a pure value object — no I/O, no DB, no network.
It answers two questions:
  1. Should we retry this exception given how many attempts we've made?
  2. How long should we wait before the next attempt?

Pre-defined policies match the settings defaults (retry_max=3) but are
independently configurable per job type. This separation allows job-level
tuning without touching adapter retry loops.

Terminal failure rule (from autonomous execution contract §5.4):
  When should_retry() returns False due to exhausted attempts or a
  non-retryable exception, the caller MUST create an exception record
  and set the scheduled_job status to 'failed'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Type


@dataclass(frozen=True)
class RetryPolicy:
    """
    Immutable retry policy for a worker job type.

    max_attempts:          total attempts allowed (not retries — includes the first try)
    base_delay_seconds:    delay after the first failure (doubles per attempt)
    retryable_types:       exception types that may be retried
                           If empty, ALL exceptions are retryable up to max_attempts.
    terminal_types:        exception types that are NEVER retried (surface immediately)

    Decision logic:
      terminal_types take priority over retryable_types.
      If the exception is terminal → never retry.
      If retryable_types is empty → retry any non-terminal exception.
      Else → only retry if isinstance(exc, retryable_types).
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    retryable_types: tuple[Type[Exception], ...] = field(default_factory=tuple)
    terminal_types: tuple[Type[Exception], ...] = field(default_factory=tuple)

    def should_retry(self, attempt: int, exc: Exception) -> bool:
        """
        Return True if the exception warrants another attempt.

        attempt: 0-indexed (0 = first attempt, 1 = first retry, …)
        """
        if attempt >= self.max_attempts - 1:
            return False
        if self.terminal_types and isinstance(exc, self.terminal_types):
            return False
        if self.retryable_types:
            return isinstance(exc, self.retryable_types)
        return True

    def delay_seconds(self, attempt: int) -> float:
        """Exponential backoff: base * 2^attempt. Capped at 1 hour."""
        raw = self.base_delay_seconds * math.pow(2, attempt)
        return min(raw, 3600.0)

    def attempts_remaining(self, attempt: int) -> int:
        """How many more attempts are possible after this one."""
        return max(0, self.max_attempts - 1 - attempt)


# ── Pre-defined policies ──────────────────────────────────────────────────────

# Default: 3 attempts, 1-second base backoff, retry any exception
DEFAULT_RETRY_POLICY = RetryPolicy(max_attempts=3, base_delay_seconds=1.0)

# GHL adapter: retry transient errors, surface auth failures immediately
GHL_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=2.0,
)

# OpenAI adapter: retry rate limits and timeouts, surface auth failures
OPENAI_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=2.0,
)

# Synthflow: retry transient errors
SYNTHFLOW_RETRY_POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=5.0,
)

# Critical path (identity resolution, Postgres state): no retries
NO_RETRY_POLICY = RetryPolicy(max_attempts=1, base_delay_seconds=0.0)
