"""
Prompt family: summary_consent_detector

Detects whether a student gave consent to receive a written summary
via email or SMS based solely on the call transcript.

Consent values:
  YES     — student explicitly agreed to receive a summary
  NO      — student declined or explicitly said they do not want one
  UNKNOWN — no clear statement; treat as NO for writeback purposes

v1: initial production version.
"""
from app.prompts.registry import register

_FAMILY = "summary_consent_detector"

register(
    family=_FAMILY,
    version="v1",
    system_prompt=(
        "You are a consent analysis tool for an admissions CRM. "
        "Analyze whether the student gave consent to receive a written summary "
        "(via email or SMS) based only on the call transcript.\n\n"
        "Return a JSON object with exactly these fields:\n"
        '- "consent": "YES" if the student explicitly agreed, '
        '"NO" if the student declined or said they do not want one, '
        '"UNKNOWN" if there is no clear statement either way\n'
        '- "confidence": "high" if the statement is unambiguous, '
        '"medium" if the statement is reasonably clear, '
        '"low" if you are uncertain\n\n'
        "Use only evidence from the transcript. "
        "If the transcript is blank or contains no relevant content, return: "
        '{"consent": "UNKNOWN", "confidence": "low"}\n\n'
        "Return only valid JSON. Do not include text outside the JSON object."
    ),
    user_prompt_template=(
        "Transcript:\n{transcript}"
    ),
)
