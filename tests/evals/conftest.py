"""
Eval harness fixtures.

EVAL_FIXTURES_LOADED: set to True when real transcript fixtures are loaded.
Until Phase 10, all evals are skipped unless EVAL_FIXTURES=1 is set.
"""
from __future__ import annotations

import os

import pytest

EVAL_FIXTURES_LOADED = os.environ.get("EVAL_FIXTURES") == "1"

skip_until_fixtures = pytest.mark.skipif(
    not EVAL_FIXTURES_LOADED,
    reason="Set EVAL_FIXTURES=1 and load fixture transcripts to run evals",
)
