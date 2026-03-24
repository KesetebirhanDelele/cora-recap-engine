"""
AI-powered message generator for SMS and email follow-ups.

Uses OpenAIClient.chat_completion() with a prompt containing:
  - campaign name
  - conversation snippets (transcripts)
  - last known lead status / tier
  - prior outbound messages (de-duplication context)

Fallback behaviour:
  Any exception (OpenAI error, missing API key, JSON decode failure,
  content truncation) silently returns the template fallback so the
  channel_jobs worker can always complete without crashing.

SMS constraint: ≤ 160 characters. If AI returns longer text, it is
truncated at word boundary to 157 chars + "...".

Injectable _client for tests:
  Pass a mock openai.OpenAI instance via _client kwarg.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.conversation_context import ConversationContext
from app.core.message_templates import EmailMessage, get_email_fallback, get_sms_fallback

logger = logging.getLogger(__name__)

SMS_MAX_CHARS = 160
_SMS_TRUNCATE_AT = 157  # leave room for "..."


def generate_sms(
    context: ConversationContext,
    settings: Any = None,
    *,
    _client: Any = None,
) -> str:
    """
    Generate a personalised SMS using the conversation context.

    Returns a ≤160 char string.
    Falls back to SMS_FALLBACK on any error.
    """
    try:
        from app.adapters.openai_client import OpenAIClient

        client = OpenAIClient(settings=settings, _client=_client)
        prompt = _build_sms_prompt(context)
        result = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=_get_model(settings),
            response_format={"type": "json_object"},
            _retry_delay=0.0,
        )
        sms_text = result.get("sms", "").strip()
        if not sms_text:
            logger.warning("generate_sms: empty response from AI | contact_id=%s", context.contact_id)
            return get_sms_fallback()

        return _truncate_sms(sms_text)

    except Exception as exc:
        logger.warning(
            "generate_sms: AI failed, using fallback | contact_id=%s: %s",
            context.contact_id, exc,
        )
        return get_sms_fallback()


def generate_email(
    context: ConversationContext,
    settings: Any = None,
    *,
    _client: Any = None,
) -> EmailMessage:
    """
    Generate a personalised email (subject + body) using the conversation context.

    Falls back to EMAIL_FALLBACK on any error.
    """
    try:
        from app.adapters.openai_client import OpenAIClient

        client = OpenAIClient(settings=settings, _client=_client)
        prompt = _build_email_prompt(context)
        result = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=_get_model(settings),
            response_format={"type": "json_object"},
            _retry_delay=0.0,
        )
        subject = result.get("subject", "").strip()
        body = result.get("body", "").strip()

        if not subject or not body:
            logger.warning(
                "generate_email: incomplete response from AI | contact_id=%s", context.contact_id
            )
            return get_email_fallback()

        return EmailMessage(subject=subject, body=body)

    except Exception as exc:
        logger.warning(
            "generate_email: AI failed, using fallback | contact_id=%s: %s",
            context.contact_id, exc,
        )
        return get_email_fallback()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_model(settings: Any) -> str:
    if settings and hasattr(settings, "openai_model_student_summary"):
        return settings.openai_model_student_summary
    return "gpt-4o-mini"


def _truncate_sms(text: str) -> str:
    if len(text) <= SMS_MAX_CHARS:
        return text
    truncated = text[:_SMS_TRUNCATE_AT]
    # Back off to last word boundary
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


def _transcript_snippet(transcripts: list[str], max_chars: int = 400) -> str:
    """Return a condensed snippet of the most recent transcript."""
    if not transcripts:
        return "(no transcript available)"
    snippet = transcripts[0][:max_chars]
    if len(transcripts[0]) > max_chars:
        snippet += "..."
    return snippet


def _build_sms_prompt(context: ConversationContext) -> str:
    campaign = context.campaign_name or "our program"
    snippet = _transcript_snippet(context.transcripts)
    prior = (
        context.outbound_messages[0]["body"]
        if context.outbound_messages
        else "(none)"
    )

    return (
        "You are writing a friendly, professional SMS follow-up for a lead who missed "
        f"a call from our outreach team about {campaign}.\n\n"
        f"Recent call transcript snippet:\n{snippet}\n\n"
        f"Last SMS we sent (avoid repetition):\n{prior}\n\n"
        "Rules:\n"
        "- Maximum 160 characters\n"
        "- Sound human, not robotic\n"
        "- Do not mention specific program details unless from the transcript\n"
        "- Do not include a URL\n"
        "- End with an open question to invite a reply\n\n"
        'Respond with JSON: {"sms": "<message text>"}'
    )


def _build_email_prompt(context: ConversationContext) -> str:
    campaign = context.campaign_name or "our program"
    snippet = _transcript_snippet(context.transcripts, max_chars=600)
    attempt_note = (
        f"This is follow-up attempt #{len(context.outbound_messages) + 1}."
        if context.outbound_messages
        else "This is the first email follow-up."
    )

    return (
        "You are writing a brief, friendly follow-up email for a lead who missed "
        f"a call from our outreach team about {campaign}.\n\n"
        f"Recent call transcript snippet:\n{snippet}\n\n"
        f"{attempt_note}\n\n"
        "Rules:\n"
        "- Subject: short, conversational (≤ 8 words)\n"
        "- Body: 2–3 short paragraphs, human tone\n"
        "- Do not include specific program details unless from the transcript\n"
        "- Do not include a URL\n"
        "- End with a soft call-to-action (reply or suggest a time)\n\n"
        'Respond with JSON: {"subject": "<subject>", "body": "<email body>"}'
    )
