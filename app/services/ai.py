"""
AI service — orchestrates prompt execution and schema validation.

Responsibilities:
  - Check for blank/unusable transcripts BEFORE calling the API (saves cost).
  - Build messages from the prompt registry using the configured family/version.
  - Call OpenAIClient.chat_completion() with json_object response format.
  - Validate and parse the response with Pydantic output schemas.
  - Stamp all outputs with model_used, prompt_family, prompt_version.

Layer rule: this module orchestrates — it does not call the OpenAI API directly.
All API calls go through app.adapters.openai_client.OpenAIClient.

Blank transcript rule (from spec):
  - generate_student_summary: blank transcript → blank SummaryOutput (no API call)
  - detect_consent: blank transcript → UNKNOWN ConsentOutput (no API call)
  - generate_call_analysis: blank transcript → still attempted (classification needed)
  - generate_voicemail_content: context-driven, not transcript-driven

Consent gate (enforced here and in the writeback path):
  - Only ConsentOutput.consent == 'YES' allows GHL summary writeback.
  - This function does NOT write to GHL — it returns the ConsentOutput.
    The caller (worker job) checks allows_writeback before writing.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.adapters.openai_client import OpenAIClient
from app.config import Settings, get_settings
from app.prompts import get_prompt
from app.schemas.ai import (
    CallAnalysisOutput,
    ConsentOutput,
    SummaryOutput,
    VMContentOutput,
    blank_summary_output,
    unknown_consent_output,
)

logger = logging.getLogger(__name__)

_BLANK_TRANSCRIPT_THRESHOLD = 10  # chars; fewer than this is treated as blank


def _is_blank(text: Optional[str]) -> bool:
    """True when transcript is absent, whitespace-only, or very short."""
    return not text or len(text.strip()) < _BLANK_TRANSCRIPT_THRESHOLD


def generate_call_analysis(
    transcript: str,
    settings: Settings | None = None,
    client: OpenAIClient | None = None,
) -> CallAnalysisOutput:
    """
    Classify a call transcript using the lead_stage_classifier prompt family.

    Returns a CallAnalysisOutput with lead_stage, call_outcome, key_topics,
    and provenance fields.
    """
    settings = settings or get_settings()
    client = client or OpenAIClient(settings=settings)

    family = settings.prompt_family_call_analysis
    version = settings.prompt_version_call_analysis
    model = settings.openai_model_call_analysis
    entry = get_prompt(family, version)

    logger.info(
        "AI call_analysis | family=%s version=%s model=%s transcript_len=%d",
        family, version, model, len(transcript or ""),
    )

    messages = entry.build_messages(transcript=transcript or "")
    raw = client.chat_completion(
        messages=messages,
        model=model,
        response_format={"type": "json_object"},
        _retry_delay=0.0 if settings.app_env == "test" else 1.0,
    )

    return CallAnalysisOutput(
        lead_stage=raw.get("lead_stage", "Unknown"),
        call_outcome=raw.get("call_outcome", "other"),
        key_topics=raw.get("key_topics", []),
        model_used=OpenAIClient._strip_prefix(model),
        prompt_family=family,
        prompt_version=version,
        raw=raw,
    )


def generate_student_summary(
    transcript: str | None,
    settings: Settings | None = None,
    client: OpenAIClient | None = None,
) -> SummaryOutput:
    """
    Generate a student-facing call summary using the student_summary_generator family.

    Blank/unusable transcripts return a blank SummaryOutput without any API call.
    This satisfies the spec requirement: blank transcript → blank summary.
    """
    settings = settings or get_settings()

    family = settings.prompt_family_student_summary
    version = settings.prompt_version_student_summary
    model = settings.openai_model_student_summary

    if _is_blank(transcript):
        logger.info(
            "AI student_summary | transcript blank — skipping API call | "
            "family=%s version=%s",
            family, version,
        )
        return blank_summary_output(
            model_used=OpenAIClient._strip_prefix(model),
            prompt_family=family,
            prompt_version=version,
        )

    client = client or OpenAIClient(settings=settings)
    entry = get_prompt(family, version)

    logger.info(
        "AI student_summary | family=%s version=%s model=%s transcript_len=%d",
        family, version, model, len(transcript),  # type: ignore[arg-type]
    )

    messages = entry.build_messages(transcript=transcript)
    raw = client.chat_completion(
        messages=messages,
        model=model,
        response_format={"type": "json_object"},
        _retry_delay=0.0 if settings.app_env == "test" else 1.0,
    )

    return SummaryOutput(
        student_summary=raw.get("student_summary", ""),
        summary_offered=bool(raw.get("summary_offered", False)),
        model_used=OpenAIClient._strip_prefix(model),
        prompt_family=family,
        prompt_version=version,
    )


def detect_consent(
    transcript: str | None,
    settings: Settings | None = None,
    client: OpenAIClient | None = None,
) -> ConsentOutput:
    """
    Detect summary consent using the summary_consent_detector prompt family.

    Blank/unusable transcripts return UNKNOWN without any API call.
    UNKNOWN consent must be treated as NO by the writeback path.

    The returned ConsentOutput.allows_writeback property encodes this gate:
      True only when consent == 'YES'.
    """
    settings = settings or get_settings()

    family = settings.prompt_family_consent
    version = settings.prompt_version_consent
    model = settings.openai_model_consent_detector

    if _is_blank(transcript):
        logger.info(
            "AI consent_detect | transcript blank — returning UNKNOWN | "
            "family=%s version=%s",
            family, version,
        )
        return unknown_consent_output(
            model_used=OpenAIClient._strip_prefix(model),
            prompt_family=family,
            prompt_version=version,
        )

    client = client or OpenAIClient(settings=settings)
    entry = get_prompt(family, version)

    logger.info(
        "AI consent_detect | family=%s version=%s model=%s transcript_len=%d",
        family, version, model, len(transcript),  # type: ignore[arg-type]
    )

    messages = entry.build_messages(transcript=transcript)
    raw = client.chat_completion(
        messages=messages,
        model=model,
        response_format={"type": "json_object"},
        _retry_delay=0.0 if settings.app_env == "test" else 1.0,
    )

    raw_consent = str(raw.get("consent", "UNKNOWN")).upper()
    if raw_consent not in {"YES", "NO", "UNKNOWN"}:
        logger.warning(
            "AI consent_detect | unexpected consent value %r — defaulting to UNKNOWN",
            raw_consent,
        )
        raw_consent = "UNKNOWN"

    raw_confidence = str(raw.get("confidence", "low")).lower()
    if raw_confidence not in {"high", "medium", "low"}:
        raw_confidence = "low"

    return ConsentOutput(
        consent=raw_consent,  # type: ignore[arg-type]
        confidence=raw_confidence,  # type: ignore[arg-type]
        model_used=OpenAIClient._strip_prefix(model),
        prompt_family=family,
        prompt_version=version,
    )


def generate_voicemail_content(
    context: str,
    settings: Settings | None = None,
    client: OpenAIClient | None = None,
) -> VMContentOutput:
    """
    Generate voicemail follow-up content using the vm_content_generator family.

    context: structured context string describing the lead and campaign.
    Returns VMContentOutput with email_html, email_subject, sms_text,
    and provenance fields.

    The returned content populates GHL contact fields used by GHL automations.
    This service does NOT send SMS or email directly.
    """
    settings = settings or get_settings()
    client = client or OpenAIClient(settings=settings)

    family = settings.prompt_family_vm_content
    version = settings.prompt_version_vm_content
    model = settings.openai_model_vm_content
    entry = get_prompt(family, version)

    logger.info(
        "AI vm_content | family=%s version=%s model=%s",
        family, version, model,
    )

    messages = entry.build_messages(context=context)
    raw = client.chat_completion(
        messages=messages,
        model=model,
        response_format={"type": "json_object"},
        _retry_delay=0.0 if settings.app_env == "test" else 1.0,
    )

    return VMContentOutput(
        email_html=raw.get("email_html", ""),
        email_subject=raw.get("email_subject", ""),
        sms_text=raw.get("sms_text", ""),
        model_used=OpenAIClient._strip_prefix(model),
        prompt_family=family,
        prompt_version=version,
    )
