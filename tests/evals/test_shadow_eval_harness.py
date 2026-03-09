"""
Shadow eval harness — Phase 5 stubs.

Documents the eval scenarios from spec/05_eval_plan.md.
All tests are skipped until EVAL_FIXTURES=1 and real fixtures are loaded.

Eval scenarios (from spec):
  1. Completed inbound call, consent YES → summary written to GHL
  2. Completed inbound call, consent NO  → no summary writeback
  3. Blank transcript → blank summary, no writeback
  4. Duplicate replay → no duplicate task or summary writeback
  5. New Lead and Cold Lead share canonical states None,0,1,2,3

Regression checklist (from spec/05_eval_plan.md):
  - Consent gate still blocks summary writeback on NO
  - Blank transcript → blank summary
  - Prompt version stamping present on all outputs
  - Model prefix stripped in model_used field

Run with:
  EVAL_FIXTURES=1 pytest tests/evals/ -v
"""
from __future__ import annotations

import pytest

from tests.evals.conftest import skip_until_fixtures

# ── Fixtures placeholder ──────────────────────────────────────────────────────
# Phase 10 will load real transcript fixtures from tests/fixtures/transcripts/.
# Each fixture is a dict: {"transcript": str, "expected": dict}

FIXTURE_TRANSCRIPTS: list[dict] = []  # populated in Phase 10


# ── Summary eval stubs ────────────────────────────────────────────────────────

@skip_until_fixtures
def test_summary_consent_yes_produces_non_blank_summary():
    """Eval: completed call with consent YES → non-blank student summary."""
    # TODO Phase 10: load fixture with consent_yes_transcript
    pytest.skip("fixture not loaded")


@skip_until_fixtures
def test_summary_consent_no_produces_summary_but_no_writeback():
    """Eval: completed call with consent NO → summary generated, writeback blocked."""
    # TODO Phase 10: load fixture with consent_no_transcript
    pytest.skip("fixture not loaded")


@skip_until_fixtures
def test_blank_transcript_produces_blank_summary():
    """Eval: blank transcript → blank SummaryOutput, no API call."""
    from app.services.ai import generate_student_summary
    result = generate_student_summary(
        transcript=None,
        settings=None,  # uses get_settings()
        client=None,
    )
    assert result.student_summary == ""
    assert result.summary_offered is False


# ── Consent eval stubs ────────────────────────────────────────────────────────

@skip_until_fixtures
def test_consent_yes_fixture():
    """Eval: transcript with explicit YES consent → ConsentOutput.consent == 'YES'."""
    pytest.skip("fixture not loaded")


@skip_until_fixtures
def test_consent_no_fixture():
    """Eval: transcript with explicit NO consent → ConsentOutput.consent == 'NO'."""
    pytest.skip("fixture not loaded")


@skip_until_fixtures
def test_consent_ambiguous_fixture():
    """Eval: ambiguous transcript → ConsentOutput.consent == 'UNKNOWN'."""
    pytest.skip("fixture not loaded")


# ── Regression checks (always run, no fixtures needed) ───────────────────────

def test_consent_gate_blocks_writeback_for_no():
    """Regression: NO consent must never allow_writeback."""
    from app.schemas.ai import ConsentOutput
    output = ConsentOutput(
        consent="NO",
        confidence="high",
        model_used="gpt-4o-mini",
        prompt_family="summary_consent_detector",
        prompt_version="v1",
    )
    assert output.allows_writeback is False


def test_consent_gate_blocks_writeback_for_unknown():
    """Regression: UNKNOWN consent must never allow_writeback."""
    from app.schemas.ai import ConsentOutput
    output = ConsentOutput(
        consent="UNKNOWN",
        confidence="low",
        model_used="gpt-4o-mini",
        prompt_family="summary_consent_detector",
        prompt_version="v1",
    )
    assert output.allows_writeback is False


def test_blank_transcript_produces_blank_summary_regression():
    """Regression: blank transcript always produces blank summary without API call."""
    from unittest.mock import MagicMock

    from app.config.settings import Settings
    from app.services.ai import generate_student_summary

    mock_client = MagicMock()
    s = Settings(_env_file=None, openai_api_key="sk-test")
    result = generate_student_summary("", settings=s, client=mock_client)
    assert result.student_summary == ""
    mock_client.chat_completion.assert_not_called()


def test_model_prefix_stripped_in_output():
    """Regression: model_used must not include 'openai/' prefix."""
    from unittest.mock import MagicMock

    from app.config.settings import Settings
    from app.services.ai import generate_student_summary

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = {
        "student_summary": "Great call!",
        "summary_offered": True,
    }
    s = Settings(
        _env_file=None,
        openai_api_key="sk-test",
        openai_model_student_summary="openai/gpt-4o-mini",
    )
    result = generate_student_summary(
        "A sufficiently long transcript to pass the blank check.",
        settings=s,
        client=mock_client,
    )
    assert not result.model_used.startswith("openai/"), (
        f"model_used must not include prefix, got: {result.model_used!r}"
    )


def test_prompt_version_stamped_on_summary():
    """Regression: prompt_family and prompt_version always stamped on SummaryOutput."""
    from unittest.mock import MagicMock

    from app.config.settings import Settings
    from app.services.ai import generate_student_summary

    mock_client = MagicMock()
    mock_client.chat_completion.return_value = {"student_summary": "s", "summary_offered": False}
    s = Settings(_env_file=None, openai_api_key="sk-test")
    result = generate_student_summary(
        "A long enough transcript for the test threshold.",
        settings=s,
        client=mock_client,
    )
    assert result.prompt_family
    assert result.prompt_version
