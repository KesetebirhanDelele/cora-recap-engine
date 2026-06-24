"""
AI-powered message generator for SMS and email follow-ups.

Two generation paths:

1. generate_vm_followup() — tier-aware, brand-context-rich generator for
   voicemail follow-up messages. Uses the vm_followup_generator prompt family
   with tier-specific versions (tier_1, tier_2, tier_3, tier_final).
   Injects brand context from app_config and student success stories from
   app/prompts/knowledge_base/video_transcripts.txt.
   Returns VmFollowupResult with full SMS + email content.

2. generate_sms() / generate_email() — generic generators used for non-VM
   contexts. These remain unchanged.

Fallback behaviour:
  Any exception silently returns the template fallback so channel_jobs
  workers can always complete without crashing.

SMS constraint for VM followups: ≤ 240 characters (per prompt spec).
Generic SMS constraint: ≤ 160 characters.

Injectable _client for tests: pass mock openai.OpenAI via _client kwarg.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.conversation_context import ConversationContext
from app.core.message_templates import EmailMessage, get_email_fallback, get_sms_fallback

logger = logging.getLogger(__name__)

SMS_MAX_CHARS = 160
_SMS_TRUNCATE_AT = 157

VM_SMS_MAX_CHARS = 240
_VM_SMS_TRUNCATE_AT = 237

# Maps attempt_number to prompt version
_ATTEMPT_TO_PROMPT_VERSION: dict[int, str] = {
    1: "tier_1",
    2: "tier_2",
    3: "tier_3",
    4: "tier_final",
}


@dataclass(frozen=True)
class GhlCallAnalysisResult:
    """Result of generate_ghl_call_analysis() for a completed call."""
    task_title: str
    task_description: str
    assign_to: str                  # GHL user ID or empty string
    is_lead_classification: bool
    lead_classification: str        # warm_lead | cold_lead | not_a_lead | ...
    create_task: bool               # True → create GHL task
    outbound_call_details: str
    call_detailed_summary: str
    ai_campaign: str                # "Yes" or "No"
    call_start_time_formatted: str
    task_due_date: str              # ISO 8601


@dataclass(frozen=True)
class VmFollowupResult:
    """Result of generate_vm_followup() — full SMS + email content for one tier."""
    sms_text: str
    email_subject: str
    email_html: str
    email_text: str
    preview_text: str
    selected_story_title: str = ""
    story_summary: str = ""
    story_link: str = ""


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


def generate_vm_followup(
    context: ConversationContext,
    settings: Any = None,
    session: Any = None,
    *,
    _client: Any = None,
) -> VmFollowupResult:
    """
    Generate a tier-aware voicemail follow-up SMS + email for a lead.

    Selects the prompt version based on context.attempt_number:
      1 → tier_1, 2 → tier_2, 3 → tier_3, 4+ → tier_final

    Brand context (sender_name, brand_name, etc.) is read from app_config
    via the session when provided, falling back to Settings/.env values.

    Student success stories are injected from knowledge_base/video_transcripts.txt.

    Returns VmFollowupResult. Falls back to template values on any error.
    """
    try:
        from app.adapters.openai_client import OpenAIClient
        from app.prompts.families import vm_followup_generator  # noqa: F401 — register prompts
        from app.prompts.knowledge_base import load_video_transcripts
        from app.prompts.registry import get_prompt

        version = _ATTEMPT_TO_PROMPT_VERSION.get(context.attempt_number, "tier_final")
        prompt_entry = get_prompt("vm_followup_generator", version)

        brand_ctx = _load_brand_context(session, settings)
        prior = _format_prior_messages(context.outbound_messages)
        ghl_thread = _format_ghl_thread(context.ghl_messages)
        video_transcripts = load_video_transcripts(sample_size=5)

        messages = prompt_entry.build_messages(
            lead_first_name=context.lead_first_name or "there",
            campaign_name=context.campaign_name or "our program",
            video_transcripts=video_transcripts,
            prior_messages=prior,
            ghl_conversation_thread=ghl_thread,
            **brand_ctx,
        )

        # Inject brand context into the system prompt too
        messages[0]["content"] = messages[0]["content"].format(
            brand_name=brand_ctx["brand_name"],
            video_transcripts=video_transcripts,
        )

        client = OpenAIClient(settings=settings, _client=_client)
        result = client.chat_completion(
            messages=messages,
            model=_get_model(settings),
            response_format={"type": "json_object"},
            _retry_delay=0.0,
        )

        sms_text = _truncate_vm_sms(result.get("sms_text", "").strip())
        email_subject = result.get("email_subject", "").strip()
        email_html = result.get("email_html", "").strip()
        email_text = result.get("email_text", "").strip()

        if not sms_text or not email_subject or not email_html:
            logger.warning(
                "generate_vm_followup: incomplete response | contact_id=%s attempt=%d",
                context.contact_id, context.attempt_number,
            )
            return _vm_fallback(settings)

        return VmFollowupResult(
            sms_text=sms_text,
            email_subject=email_subject,
            email_html=email_html,
            email_text=email_text or email_html,
            preview_text=result.get("preview_text", "").strip(),
            selected_story_title=result.get("selected_story_title", "").strip(),
            story_summary=result.get("story_summary", "").strip(),
            story_link=result.get("story_link", "").strip(),
        )

    except Exception as exc:
        logger.warning(
            "generate_vm_followup: AI failed, using fallback | contact_id=%s: %s",
            context.contact_id, exc,
        )
        return _vm_fallback(settings)


def generate_ghl_call_analysis(
    transcript: str,
    call_start_time_ms: int | None,
    duration_seconds: int | None,
    contact_phone: str,
    settings: Any = None,
    session: Any = None,
    *,
    _client: Any = None,
) -> GhlCallAnalysisResult:
    """
    Generate a rich GHL call analysis from a completed call transcript.

    Returns GhlCallAnalysisResult with task title, description, assignment,
    lead classification, AI campaign flag, and due date.

    Falls back to a safe default result on any error so callers always
    get a usable output without crashing the worker.
    """
    try:
        from app.adapters.openai_client import OpenAIClient
        from app.prompts.families import ghl_call_analysis  # noqa: F401 — register
        from app.prompts.registry import get_prompt

        prompt_entry = get_prompt("ghl_call_analysis", "v1")
        messages = prompt_entry.build_messages(
            call_start_time_ms=str(call_start_time_ms or 0),
            duration_seconds=str(duration_seconds or 0),
            contact_phone=contact_phone or "",
            transcript=transcript or "(no transcript)",
        )

        admissions_ghl_id = _load_admissions_ghl_id(session, settings)
        messages[0]["content"] = messages[0]["content"].replace(
            "ADMISSIONS_GHL_ID_HERE", admissions_ghl_id
        )

        model = (
            getattr(settings, "openai_model_ghl_analysis", None)
            or "gpt-4o-mini"
        )
        client = OpenAIClient(settings=settings, _client=_client)
        result = client.chat_completion(
            messages=messages,
            model=model,
            response_format={"type": "json_object"},
            _retry_delay=0.0,
        )

        return GhlCallAnalysisResult(
            task_title=result.get("task_title", "Follow-Up").strip(),
            task_description=result.get("task_description", "").strip(),
            assign_to=result.get("assign_to", "").strip(),
            is_lead_classification=bool(result.get("is_lead_classification", False)),
            lead_classification=result.get("lead_classification", "not_a_lead").strip(),
            create_task=str(result.get("create_task", "no")).lower() == "yes",
            outbound_call_details=result.get("outbound_call_details", "").strip(),
            call_detailed_summary=result.get("call_detailed_summary", "").strip(),
            ai_campaign=result.get("ai_campaign", "No").strip(),
            call_start_time_formatted=result.get("call_start_time_formatted", "").strip(),
            task_due_date=result.get("task_due_date", "").strip(),
        )

    except Exception as exc:
        logger.warning(
            "generate_ghl_call_analysis: AI failed, using fallback | phone=%s: %s",
            contact_phone, exc,
        )
        return GhlCallAnalysisResult(
            task_title="Follow-Up: Completed Call",
            task_description="Automated follow-up — analysis unavailable.",
            assign_to="",
            is_lead_classification=False,
            lead_classification="not_a_lead",
            create_task=False,
            outbound_call_details="",
            call_detailed_summary="",
            ai_campaign="No",
            call_start_time_formatted="",
            task_due_date="",
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _get_model(settings: Any) -> str:
    if settings and hasattr(settings, "openai_model_student_summary"):
        return settings.openai_model_student_summary
    return "gpt-4o-mini"


def _truncate_vm_sms(text: str) -> str:
    if len(text) <= VM_SMS_MAX_CHARS:
        return text
    truncated = text[:_VM_SMS_TRUNCATE_AT]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "..."


_ADMISSIONS_FALLBACK_GHL_ID = "0swBv9tBNeXeYXPYFBSx"  # Roselen Flores


def _load_admissions_ghl_id(session: Any, settings: Any) -> str:
    """
    Return the GHL user ID for the primary admissions assistant.

    Reads the 'admissions_assistants' key from app_config (JSON list of
    {"name": str, "ghl_id": str}).  The first entry is the active assignee.
    Falls back to the hardcoded default when the key is absent or unreadable.
    """
    import json

    raw = None
    try:
        if session is not None:
            from app.core.app_config import get_str
            raw = get_str("admissions_assistants", session, settings, "")
        elif settings is not None:
            raw = str(getattr(settings, "admissions_assistants", None) or "")
    except Exception:
        logger.warning("_load_admissions_ghl_id: failed to read config — using fallback")

    if raw:
        try:
            assistants = json.loads(raw)
            if assistants and isinstance(assistants, list):
                ghl_id = assistants[0].get("ghl_id", "").strip()
                if ghl_id:
                    return ghl_id
        except (json.JSONDecodeError, AttributeError, IndexError):
            logger.warning("_load_admissions_ghl_id: malformed JSON — using fallback")

    return _ADMISSIONS_FALLBACK_GHL_ID


def _load_brand_context(session: Any, settings: Any) -> dict[str, str]:
    """
    Build the brand context dict for prompt injection.
    Reads from app_config (DB-first) with .env fallback.
    """
    if session is not None:
        from app.core.app_config import get_str

        def _gs(key: str, default: str = "") -> str:
            return get_str(key, session, settings, default)
    else:
        def _gs(key: str, default: str = "") -> str:  # type: ignore[misc]
            return str(getattr(settings, key, None) or default)

    return {
        "brand_name":                 _gs("brand_name", "Colaberry"),
        "sender_name":                _gs("sender_name", "Cora from Colaberry"),
        "reply_to_email":             _gs("reply_to_email", "admissions@colaberry.com"),
        "next_class_start":           _gs("next_class_start", "upcoming"),
        "live_open_house_link":       _gs("live_open_house_link", ""),
        "explainer_video_link":       _gs("explainer_open_house_video_link", ""),
        "unsubscribe_text":           _gs("unsubscribe_text", "Text STOP to stop alerts"),
    }


def _format_prior_messages(outbound_messages: list[dict]) -> str:
    if not outbound_messages:
        return "(none sent yet)"
    lines = []
    for m in outbound_messages[:3]:  # last 3 only to keep prompt concise
        ch = m.get("channel", "?")
        body = (m.get("body") or "")[:200]
        lines.append(f"[{ch}] {body}")
    return "\n".join(lines)


def _format_ghl_thread(ghl_messages: list[dict]) -> str:
    """
    Format GHL two-way conversation messages for prompt injection.

    Shows newest-last so the AI reads the thread chronologically.
    Caps at 8 messages and 200 chars per body to stay within token budget.
    Returns a placeholder string when no messages are available.
    """
    if not ghl_messages:
        return "(no GHL conversation history)"
    # ghl_messages arrive newest-first from the API; reverse for chronological order.
    recent = list(reversed(ghl_messages[-8:]))
    lines = []
    for msg in recent:
        direction = msg.get("direction", "outbound")
        msg_type = msg.get("type", "SMS")
        date = (msg.get("date") or "")[:10]  # YYYY-MM-DD only
        body = (msg.get("body") or "")[:200]
        tag = "LEAD" if direction == "inbound" else "US"
        lines.append(f"[{tag} {msg_type} {date}] {body}")
    return "\n".join(lines)


def _vm_fallback(settings: Any) -> VmFollowupResult:
    fb_sms = get_sms_fallback()
    fb_email = get_email_fallback()
    return VmFollowupResult(
        sms_text=fb_sms,
        email_subject=fb_email.subject,
        email_html=f"<p>{fb_email.body.replace(chr(10), '</p><p>')}</p>",
        email_text=fb_email.body,
        preview_text="",
    )


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
    ghl_thread = _format_ghl_thread(context.ghl_messages)

    return (
        "You are writing a friendly, professional SMS follow-up for a lead who missed "
        f"a call from our outreach team about {campaign}.\n\n"
        f"Recent call transcript snippet:\n{snippet}\n\n"
        f"Previous two-way conversation (newest at bottom):\n{ghl_thread}\n\n"
        f"Last SMS we sent (avoid repetition):\n{prior}\n\n"
        "Rules:\n"
        "- Maximum 160 characters\n"
        "- Sound human, not robotic\n"
        "- Acknowledge anything the lead has already replied with\n"
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
    ghl_thread = _format_ghl_thread(context.ghl_messages)

    return (
        "You are writing a brief, friendly follow-up email for a lead who missed "
        f"a call from our outreach team about {campaign}.\n\n"
        f"Recent call transcript snippet:\n{snippet}\n\n"
        f"Previous two-way conversation (newest at bottom):\n{ghl_thread}\n\n"
        f"{attempt_note}\n\n"
        "Rules:\n"
        "- Subject: short, conversational (≤ 8 words)\n"
        "- Body: 2–3 short paragraphs, human tone\n"
        "- Acknowledge anything the lead has already replied with\n"
        "- Do not include specific program details unless from the transcript\n"
        "- Do not include a URL\n"
        "- End with a soft call-to-action (reply or suggest a time)\n\n"
        'Respond with JSON: {"subject": "<subject>", "body": "<email body>"}'
    )
