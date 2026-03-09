"""
Pydantic output schemas for AI service responses.

All outputs are stamped with model_used, prompt_family, prompt_version
for auditability and rollback capability.

Consent values: 'YES' | 'NO' | 'UNKNOWN'
  - YES     → summary writeback to GHL is allowed
  - NO      → writeback must NOT occur (spec must-not)
  - UNKNOWN → treat as NO; do not write

These schemas validate AI API responses before they are stored in the DB
or used for downstream decisions.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


class CallAnalysisOutput(BaseModel):
    """Output of the lead_stage_classifier prompt family."""

    # lead_stage: the CRM lead stage classification
    lead_stage: str
    # call_outcome: completed | voicemail | no_answer | hangup | other
    call_outcome: str
    key_topics: list[str] = []
    # provenance stamping
    model_used: str
    prompt_family: str
    prompt_version: str
    # raw: the full parsed JSON from the model (preserved for audit)
    raw: dict = {}


class SummaryOutput(BaseModel):
    """Output of the student_summary_generator prompt family."""

    # student_summary: blank string when transcript is blank or unusable
    student_summary: str
    summary_offered: bool
    # provenance stamping
    model_used: str
    prompt_family: str
    prompt_version: str

    @field_validator("student_summary")
    @classmethod
    def normalize_blank(cls, v: str) -> str:
        """Normalize whitespace-only summaries to empty string."""
        return v.strip() if v else ""


class ConsentOutput(BaseModel):
    """Output of the summary_consent_detector prompt family."""

    # consent: YES | NO | UNKNOWN (treat UNKNOWN as NO for writeback)
    consent: Literal["YES", "NO", "UNKNOWN"]
    confidence: Literal["high", "medium", "low"] = "low"
    # provenance stamping
    model_used: str
    prompt_family: str
    prompt_version: str

    @property
    def allows_writeback(self) -> bool:
        """True only when consent is explicitly YES."""
        return self.consent == "YES"


class VMContentOutput(BaseModel):
    """Output of the vm_content_generator prompt family."""

    email_html: str = ""
    email_subject: str = ""
    sms_text: str = ""
    # provenance stamping
    model_used: str
    prompt_family: str
    prompt_version: str

    @field_validator("sms_text")
    @classmethod
    def sms_length_warning(cls, v: str) -> str:
        """Log a warning if SMS text exceeds 160 characters (not an error)."""
        if len(v) > 160:
            import logging
            logging.getLogger(__name__).warning(
                "vm_content sms_text exceeds 160 chars: %d chars", len(v)
            )
        return v


# ── Blank-transcript sentinel output factories ─────────────────────────────────

def blank_summary_output(model_used: str, prompt_family: str, prompt_version: str) -> SummaryOutput:
    """Return the canonical blank-transcript summary response (no API call)."""
    return SummaryOutput(
        student_summary="",
        summary_offered=False,
        model_used=model_used,
        prompt_family=prompt_family,
        prompt_version=prompt_version,
    )


def unknown_consent_output(model_used: str, prompt_family: str, prompt_version: str) -> ConsentOutput:
    """Return the canonical unknown-consent response (no API call needed)."""
    return ConsentOutput(
        consent="UNKNOWN",
        confidence="low",
        model_used=model_used,
        prompt_family=prompt_family,
        prompt_version=prompt_version,
    )
