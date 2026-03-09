"""
Phase 5 — AI service tests.

OpenAIClient is mocked — no real API calls.

Covers:
  1.  generate_student_summary: blank transcript returns blank SummaryOutput, no API call
  2.  generate_student_summary: whitespace-only transcript treated as blank
  3.  generate_student_summary: very short transcript treated as blank
  4.  generate_student_summary: real transcript calls API and returns stamped output
  5.  generate_student_summary: model and prompt version stamped on output
  6.  detect_consent: blank transcript returns UNKNOWN, no API call
  7.  detect_consent: YES consent returned correctly
  8.  detect_consent: NO consent returned correctly
  9.  detect_consent: UNKNOWN consent returned and allows_writeback is False
  10. detect_consent: unexpected consent value defaults to UNKNOWN
  11. detect_consent: model and prompt version stamped
  12. generate_call_analysis: returns lead_stage and call_outcome
  13. generate_call_analysis: stamped with model/version
  14. generate_voicemail_content: returns email + sms fields
  15. generate_voicemail_content: stamped with model/version
  16. ConsentOutput.allows_writeback: True only for YES
  17. SummaryOutput.normalize_blank: whitespace-only summary normalized to empty string
  18. blank_summary_output / unknown_consent_output factories produce correct defaults
  19. OpenAIClient._strip_prefix strips 'openai/' prefix
  20. OpenAIClient: validate_for_openai called (raises ConfigError if no key)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.config.settings import Settings
from app.schemas.ai import (
    ConsentOutput,
    SummaryOutput,
    blank_summary_output,
    unknown_consent_output,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _settings(**overrides) -> Settings:
    defaults = dict(
        openai_api_key="sk-test",
        openai_model_call_analysis="gpt-4o-mini",
        openai_model_student_summary="gpt-4o-mini",
        openai_model_consent_detector="gpt-4o-mini",
        openai_model_vm_content="gpt-4o-mini",
        prompt_family_call_analysis="lead_stage_classifier",
        prompt_version_call_analysis="v1",
        prompt_family_student_summary="student_summary_generator",
        prompt_version_student_summary="v1",
        prompt_family_consent="summary_consent_detector",
        prompt_version_consent="v1",
        prompt_family_vm_content="vm_content_generator",
        prompt_version_vm_content="v1",
        app_env="test",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _mock_client(response_dict: dict) -> MagicMock:
    """Build a mock OpenAIClient that returns response_dict from chat_completion."""
    client = MagicMock()
    client.chat_completion.return_value = response_dict
    return client


# ─────────────────────────────────────────────────────────────────────────────
# 1-3. generate_student_summary — blank transcript guard
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("blank_input", [None, "", "   ", "\n\t", "short"])
def test_summary_blank_transcript_no_api_call(blank_input):
    from app.services.ai import generate_student_summary
    mock_client = _mock_client({})
    s = _settings()
    result = generate_student_summary(blank_input, settings=s, client=mock_client)
    mock_client.chat_completion.assert_not_called()
    assert result.student_summary == ""
    assert result.summary_offered is False


def test_summary_blank_returns_summary_output_instance():
    from app.services.ai import generate_student_summary
    s = _settings()
    result = generate_student_summary(None, settings=s, client=_mock_client({}))
    assert isinstance(result, SummaryOutput)


# ─────────────────────────────────────────────────────────────────────────────
# 4-5. generate_student_summary — real transcript path
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_real_transcript_calls_api():
    from app.services.ai import generate_student_summary
    mock_client = _mock_client({"student_summary": "Great call!", "summary_offered": True})
    s = _settings()
    result = generate_student_summary(
        "This is a sufficiently long transcript for the test.", settings=s, client=mock_client
    )
    mock_client.chat_completion.assert_called_once()
    assert result.student_summary == "Great call!"
    assert result.summary_offered is True


def test_summary_stamped_with_model_and_version():
    from app.services.ai import generate_student_summary
    mock_client = _mock_client({"student_summary": "Summary text.", "summary_offered": False})
    s = _settings()
    result = generate_student_summary(
        "A sufficiently long real transcript here.", settings=s, client=mock_client
    )
    assert result.model_used == "gpt-4o-mini"
    assert result.prompt_family == "student_summary_generator"
    assert result.prompt_version == "v1"


def test_summary_model_prefix_stripped():
    from app.services.ai import generate_student_summary
    mock_client = _mock_client({"student_summary": "Summary.", "summary_offered": False})
    s = _settings(openai_model_student_summary="openai/gpt-4o-mini")
    result = generate_student_summary(
        "A real transcript of sufficient length here.", settings=s, client=mock_client
    )
    assert result.model_used == "gpt-4o-mini"  # prefix stripped


# ─────────────────────────────────────────────────────────────────────────────
# 6-11. detect_consent
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("blank_input", [None, "", "   ", "short"])
def test_consent_blank_transcript_no_api_call(blank_input):
    from app.services.ai import detect_consent
    mock_client = _mock_client({})
    s = _settings()
    result = detect_consent(blank_input, settings=s, client=mock_client)
    mock_client.chat_completion.assert_not_called()
    assert result.consent == "UNKNOWN"
    assert result.allows_writeback is False


def test_consent_yes_returned():
    from app.services.ai import detect_consent
    mock_client = _mock_client({"consent": "YES", "confidence": "high"})
    s = _settings()
    result = detect_consent(
        "Yes, please send me the summary. I would love to receive it.",
        settings=s, client=mock_client,
    )
    assert result.consent == "YES"
    assert result.allows_writeback is True
    assert result.confidence == "high"


def test_consent_no_returned():
    from app.services.ai import detect_consent
    mock_client = _mock_client({"consent": "NO", "confidence": "high"})
    s = _settings()
    result = detect_consent(
        "No thanks, I do not want to receive a summary via email.",
        settings=s, client=mock_client,
    )
    assert result.consent == "NO"
    assert result.allows_writeback is False


def test_consent_unknown_allows_writeback_false():
    from app.services.ai import detect_consent
    mock_client = _mock_client({"consent": "UNKNOWN", "confidence": "low"})
    s = _settings()
    result = detect_consent(
        "Hmm, whatever you think is best I guess.",
        settings=s, client=mock_client,
    )
    assert result.consent == "UNKNOWN"
    assert result.allows_writeback is False


def test_consent_unexpected_value_defaults_to_unknown():
    from app.services.ai import detect_consent
    mock_client = _mock_client({"consent": "MAYBE", "confidence": "medium"})
    s = _settings()
    result = detect_consent(
        "A real transcript with enough content for the test threshold here.",
        settings=s, client=mock_client,
    )
    assert result.consent == "UNKNOWN"
    assert result.allows_writeback is False


def test_consent_stamped_with_version():
    from app.services.ai import detect_consent
    mock_client = _mock_client({"consent": "YES", "confidence": "high"})
    s = _settings()
    result = detect_consent(
        "Yes, please send me the summary by email.", settings=s, client=mock_client
    )
    assert result.prompt_family == "summary_consent_detector"
    assert result.prompt_version == "v1"
    assert result.model_used == "gpt-4o-mini"


# ─────────────────────────────────────────────────────────────────────────────
# 12-13. generate_call_analysis
# ─────────────────────────────────────────────────────────────────────────────

def test_call_analysis_returns_lead_stage_and_outcome():
    from app.services.ai import generate_call_analysis
    mock_client = _mock_client({
        "lead_stage": "Cold Lead",
        "call_outcome": "voicemail",
        "key_topics": ["program inquiry", "financial aid"],
    })
    s = _settings()
    result = generate_call_analysis("Some transcript.", settings=s, client=mock_client)
    assert result.lead_stage == "Cold Lead"
    assert result.call_outcome == "voicemail"
    assert "program inquiry" in result.key_topics


def test_call_analysis_stamped():
    from app.services.ai import generate_call_analysis
    mock_client = _mock_client({"lead_stage": "New Lead", "call_outcome": "completed"})
    s = _settings()
    result = generate_call_analysis("Transcript here.", settings=s, client=mock_client)
    assert result.model_used == "gpt-4o-mini"
    assert result.prompt_family == "lead_stage_classifier"
    assert result.prompt_version == "v1"


def test_call_analysis_missing_keys_use_defaults():
    from app.services.ai import generate_call_analysis
    mock_client = _mock_client({})  # empty response
    s = _settings()
    result = generate_call_analysis("Transcript.", settings=s, client=mock_client)
    assert result.lead_stage == "Unknown"
    assert result.call_outcome == "other"
    assert result.key_topics == []


# ─────────────────────────────────────────────────────────────────────────────
# 14-15. generate_voicemail_content
# ─────────────────────────────────────────────────────────────────────────────

def test_vm_content_returns_all_fields():
    from app.services.ai import generate_voicemail_content
    mock_client = _mock_client({
        "email_subject": "We tried to reach you!",
        "email_html": "<p>Hi there</p>",
        "sms_text": "Hi, we called. Call us back!",
    })
    s = _settings()
    result = generate_voicemail_content("Cold Lead context.", settings=s, client=mock_client)
    assert result.email_subject == "We tried to reach you!"
    assert "<p>" in result.email_html
    assert result.sms_text == "Hi, we called. Call us back!"


def test_vm_content_stamped():
    from app.services.ai import generate_voicemail_content
    mock_client = _mock_client({"email_subject": "s", "email_html": "h", "sms_text": "t"})
    s = _settings()
    result = generate_voicemail_content("context", settings=s, client=mock_client)
    assert result.prompt_family == "vm_content_generator"
    assert result.prompt_version == "v1"
    assert result.model_used == "gpt-4o-mini"


def test_vm_content_missing_fields_default_to_empty():
    from app.services.ai import generate_voicemail_content
    mock_client = _mock_client({})
    s = _settings()
    result = generate_voicemail_content("context", settings=s, client=mock_client)
    assert result.email_subject == ""
    assert result.email_html == ""
    assert result.sms_text == ""


# ─────────────────────────────────────────────────────────────────────────────
# 16. ConsentOutput.allows_writeback
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("consent,expected", [
    ("YES", True),
    ("NO", False),
    ("UNKNOWN", False),
])
def test_allows_writeback_property(consent, expected):
    output = ConsentOutput(
        consent=consent,
        confidence="high",
        model_used="gpt-4o-mini",
        prompt_family="summary_consent_detector",
        prompt_version="v1",
    )
    assert output.allows_writeback is expected


# ─────────────────────────────────────────────────────────────────────────────
# 17. SummaryOutput.normalize_blank
# ─────────────────────────────────────────────────────────────────────────────

def test_summary_whitespace_normalized_to_empty():
    output = SummaryOutput(
        student_summary="   \n  ",
        summary_offered=False,
        model_used="gpt-4o-mini",
        prompt_family="student_summary_generator",
        prompt_version="v1",
    )
    assert output.student_summary == ""


def test_summary_real_content_preserved():
    output = SummaryOutput(
        student_summary="  Great call!  ",
        summary_offered=True,
        model_used="gpt-4o-mini",
        prompt_family="student_summary_generator",
        prompt_version="v1",
    )
    assert output.student_summary == "Great call!"


# ─────────────────────────────────────────────────────────────────────────────
# 18. Factory functions
# ─────────────────────────────────────────────────────────────────────────────

def test_blank_summary_output_factory():
    result = blank_summary_output("gpt-4o-mini", "student_summary_generator", "v1")
    assert result.student_summary == ""
    assert result.summary_offered is False
    assert result.model_used == "gpt-4o-mini"


def test_unknown_consent_output_factory():
    result = unknown_consent_output("gpt-4o-mini", "summary_consent_detector", "v1")
    assert result.consent == "UNKNOWN"
    assert result.allows_writeback is False
    assert result.confidence == "low"


# ─────────────────────────────────────────────────────────────────────────────
# 19. _strip_prefix
# ─────────────────────────────────────────────────────────────────────────────

def test_strip_prefix_removes_openai_slash():
    from app.adapters.openai_client import OpenAIClient
    assert OpenAIClient._strip_prefix("openai/gpt-4o-mini") == "gpt-4o-mini"


def test_strip_prefix_leaves_plain_model_name():
    from app.adapters.openai_client import OpenAIClient
    assert OpenAIClient._strip_prefix("gpt-4o-mini") == "gpt-4o-mini"


def test_strip_prefix_leaves_other_prefix():
    from app.adapters.openai_client import OpenAIClient
    assert OpenAIClient._strip_prefix("anthropic/claude-3") == "anthropic/claude-3"


# ─────────────────────────────────────────────────────────────────────────────
# 20. OpenAIClient: validate_for_openai called
# ─────────────────────────────────────────────────────────────────────────────

def test_openai_client_raises_config_error_without_api_key():
    from app.adapters.openai_client import OpenAIClient
    from app.config.settings import ConfigError
    s = Settings(_env_file=None)  # no api key
    mock_oai = MagicMock()
    client = OpenAIClient(settings=s, _client=mock_oai)
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        client.chat_completion([{"role": "user", "content": "test"}], model="gpt-4o-mini")
    mock_oai.chat.completions.create.assert_not_called()
