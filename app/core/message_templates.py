"""
Fallback message templates for SMS and email.

Used when AI generation fails or is unavailable.
These are intentionally short and generic — they do not reference
specific program details that could be wrong without context.
"""
from __future__ import annotations

from dataclasses import dataclass


SMS_FALLBACK: str = (
    "Hey — tried calling you earlier. When's a good time to connect?"
)

EMAIL_SUBJECT_FALLBACK: str = "Quick follow-up"

EMAIL_BODY_FALLBACK: str = (
    "Hi,\n\n"
    "I tried reaching you earlier and wanted to follow up.\n\n"
    "Let me know a good time to connect — happy to work around your schedule.\n\n"
    "Best,"
)


@dataclass(frozen=True)
class EmailMessage:
    subject: str
    body: str


def get_sms_fallback() -> str:
    return SMS_FALLBACK


def get_email_fallback() -> EmailMessage:
    return EmailMessage(subject=EMAIL_SUBJECT_FALLBACK, body=EMAIL_BODY_FALLBACK)
