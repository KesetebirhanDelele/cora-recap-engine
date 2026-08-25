"""
Unit tests for app.core.call_quality_scoring.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.core.call_quality_scoring import format_support_context, score_call

# ─────────────────────────────────────────────────────────────────────────────
# format_support_context
# ─────────────────────────────────────────────────────────────────────────────

def test_format_support_context_empty():
    result = format_support_context({})
    assert result == "No prior support-ticket context available in GHL."


def test_format_support_context_includes_categories():
    result = format_support_context({"issue_categories": ["Billing", "Technical"]})
    assert "Billing, Technical" in result


def test_format_support_context_includes_descriptions():
    result = format_support_context({"issue_descriptions": ["Can't log in"]})
    assert "Can't log in" in result


def test_format_support_context_includes_satisfaction():
    result = format_support_context({
        "student_satisfaction": {"1. How satisfied were you with the resolution of your issue?": "Very satisfied"}
    })
    assert "Very satisfied" in result


def test_format_support_context_skips_empty_satisfaction_answers():
    result = format_support_context({"student_satisfaction": {"Q1": ""}})
    assert result == "No prior support-ticket context available in GHL."


# ─────────────────────────────────────────────────────────────────────────────
# score_call — outcome gate
# ─────────────────────────────────────────────────────────────────────────────

def test_score_call_returns_none_when_not_connected():
    client = MagicMock()
    result = score_call(
        transcript="hello", conversation_type="sales",
        duration_seconds=45, call_connected=False, client=client,
    )
    assert result is None
    client.chat_completion.assert_not_called()


def test_score_call_returns_none_when_duration_too_short():
    client = MagicMock()
    result = score_call(
        transcript="hello", conversation_type="sales",
        duration_seconds=5, call_connected=True, client=client,
    )
    assert result is None
    client.chat_completion.assert_not_called()


def test_score_call_returns_none_when_duration_missing():
    client = MagicMock()
    result = score_call(
        transcript="hello", conversation_type="sales",
        duration_seconds=None, call_connected=True, client=client,
    )
    assert result is None


def test_score_call_returns_none_for_other_conversation_type():
    client = MagicMock()
    result = score_call(
        transcript="hello", conversation_type="other",
        duration_seconds=60, call_connected=True, client=client,
    )
    assert result is None
    client.chat_completion.assert_not_called()


def test_score_call_returns_none_for_unknown_conversation_type():
    client = MagicMock()
    result = score_call(
        transcript="hello", conversation_type="unknown",
        duration_seconds=60, call_connected=True, client=client,
    )
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# score_call — sales rubric
# ─────────────────────────────────────────────────────────────────────────────

def test_score_call_sales_invokes_chat_completion():
    client = MagicMock()
    client.chat_completion.return_value = {"overall_score": 85, "summary": "Good call"}

    result = score_call(
        transcript="rep and lead talk about pricing",
        conversation_type="sales",
        duration_seconds=120, call_connected=True, client=client,
    )

    assert result == {"overall_score": 85, "summary": "Good call"}
    prompt = client.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert "sales/admissions" in prompt
    assert "compliance" in prompt.lower()


def test_score_call_sales_truncates_transcript():
    client = MagicMock()
    client.chat_completion.return_value = {"overall_score": 50}
    long_transcript = "y" * 50000

    score_call(
        transcript=long_transcript, conversation_type="sales",
        duration_seconds=60, call_connected=True, client=client,
    )

    prompt = client.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert len(prompt) < 50000


# ─────────────────────────────────────────────────────────────────────────────
# score_call — support rubric
# ─────────────────────────────────────────────────────────────────────────────

def test_score_call_support_includes_context():
    client = MagicMock()
    client.chat_completion.return_value = {"overall_score": 70}

    score_call(
        transcript="student calls about billing",
        conversation_type="support",
        duration_seconds=90, call_connected=True, client=client,
        support_context={"issue_categories": ["Billing"]},
    )

    prompt = client.chat_completion.call_args.kwargs["messages"][0]["content"]
    assert "Billing" in prompt
    assert "identified_request" in prompt


def test_score_call_support_works_without_context():
    client = MagicMock()
    client.chat_completion.return_value = {"overall_score": 70}

    result = score_call(
        transcript="student calls about billing",
        conversation_type="support",
        duration_seconds=90, call_connected=True, client=client,
    )

    assert result == {"overall_score": 70}


# ─────────────────────────────────────────────────────────────────────────────
# score_call — AI failure handling
# ─────────────────────────────────────────────────────────────────────────────

def test_score_call_handles_openai_error_without_raising():
    from app.adapters.openai_client import OpenAIError

    client = MagicMock()
    client.chat_completion.side_effect = OpenAIError("rate limited")

    result = score_call(
        transcript="hello", conversation_type="sales",
        duration_seconds=60, call_connected=True, client=client,
    )

    assert result["overall_score"] is None
    assert "rate limited" in result["flagged_reason"]
