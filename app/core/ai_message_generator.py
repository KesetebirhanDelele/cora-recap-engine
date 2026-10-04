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

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.core.conversation_context import ConversationContext
from app.core.message_templates import EmailMessage, get_email_fallback, get_sms_fallback

logger = logging.getLogger(__name__)

SMS_MAX_CHARS = 160
_SMS_TRUNCATE_AT = 157

VM_SMS_MAX_CHARS = 240
SMS_ONE_SEGMENT_TARGET = 160     # first draft should fit one segment; two segments (240) is the ceiling
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
    channel: str | None = None,
    _client: Any = None,
) -> VmFollowupResult:
    """
    Tier-aware voicemail follow-up for a lead (spec/37).

    channel="sms"   -> only the SMS (a missed-call NOTIFICATION, prompt family vm_sms_notice)
    channel="email" -> only the email (the program description, prompt family vm_followup_generator)
    channel=None    -> both (legacy callers)
    Prompt version by context.attempt_number: 1 tier_1, 2 tier_2, 3 tier_3, 4+ tier_final.
    Every result is checked for forbidden (retired-course) wording and, for SMS, marketing wording and length;
    one corrective retry, then a safe deterministic fallback. The pre-send SMS gate checks again.
    """
    sms_text = ""
    email = None
    if channel in (None, "sms"):
        sms_text = generate_vm_sms(context, settings, session, _client=_client)
    if channel in (None, "email"):
        email = generate_vm_email(context, settings, session, _client=_client)
    if email is None:
        return VmFollowupResult(sms_text=sms_text, email_subject="", email_html="", email_text="", preview_text="")
    return VmFollowupResult(
        sms_text=sms_text, email_subject=email["email_subject"], email_html=email["email_html"],
        email_text=email["email_text"], preview_text=email["preview_text"],
    )


def _offer_terms(session: Any, settings: Any) -> tuple[str, ...]:
    from app.core import offer

    raw: str | None = offer.DEFAULT_FORBIDDEN_TERMS
    try:
        if session is not None:
            from app.core.app_config import get_str

            raw = get_str("offer_forbidden_terms", session, settings, offer.DEFAULT_FORBIDDEN_TERMS)
        elif settings is not None and getattr(settings, "offer_forbidden_terms", None) is not None:
            raw = str(settings.offer_forbidden_terms)
    except Exception:
        logger.warning("offer_forbidden_terms unreadable - using defaults")
    return offer.parse_terms(raw)


def _finish_vm_sms(text: str, brand: dict[str, str]) -> str:
    """Plain GSM text that ends with the opt-out line and fits VM_SMS_MAX_CHARS."""
    from app.core.sms_gate import normalize_sms

    optout = normalize_sms(brand["unsubscribe_text"]).strip()
    body = normalize_sms(text)
    if optout and body.lower().rstrip(". ").endswith(optout.lower().rstrip(". ")):
        body = body[: len(body.rstrip(". ")) - len(optout.rstrip(". "))].rstrip(" .,-")
    room = VM_SMS_MAX_CHARS - len(optout) - 1 if optout else VM_SMS_MAX_CHARS
    if len(body) > room:
        body = body[:room].rsplit(" ", 1)[0].rstrip(" .,-") + "."
    if body and body[-1] not in ".!?":
        body += "."                                   # "...a good time. Text STOP to stop alerts"
    return f"{body} {optout}".strip()


def _sms_problems(text: str, terms: tuple[str, ...]) -> list[str]:
    from app.core import sms_gate

    cfg = sms_gate.GateConfig(forbidden_terms=terms, notification_only=True, allowed_link_domains="")
    return sms_gate.content_violations(text, cfg)


def _sms_notice_fallback(context: ConversationContext, brand: dict[str, str]) -> str:
    first = (context.lead_first_name or "there").strip() or "there"
    return _finish_vm_sms(
        f"Hi {first}, it's Cora from {brand['brand_name']}. I tried calling about the "
        f"{brand['offer_name']}. What time works to talk?", brand)       # ~150 chars with the opt-out line: one segment


def generate_vm_sms(
    context: ConversationContext, settings: Any = None, session: Any = None, *, _client: Any = None,
) -> str:
    """The missed-call SMS: a short notification, never marketing (spec/37). Always returns sendable text."""
    brand: dict[str, str] = {}
    try:
        from app.adapters.openai_client import OpenAIClient
        from app.prompts.families import vm_sms_notice  # noqa: F401 - register prompts
        from app.prompts.registry import get_prompt

        brand = _load_brand_context(session, settings)
        terms = _offer_terms(session, settings)
        version = _ATTEMPT_TO_PROMPT_VERSION.get(context.attempt_number, "tier_final")
        entry = get_prompt("vm_sms_notice", version)
        messages = entry.build_messages(
            lead_first_name=context.lead_first_name or "there",
            prior_messages=_format_prior_messages(context.outbound_messages),
            ghl_conversation_thread=_format_ghl_thread(context.ghl_messages),
            **brand,
        )
        messages[0]["content"] = messages[0]["content"].format(
            brand_name=brand["brand_name"], offer_name=brand["offer_name"])
        client = OpenAIClient(settings=settings, _client=_client)
        for attempt in range(3):
            result = client.chat_completion(
                messages=messages, model=_get_model(settings),
                response_format={"type": "json_object"}, _retry_delay=0.0)
            text = _finish_vm_sms(str(result.get("sms_text", "")), brand)
            problems = _sms_problems(text, terms)
            if text and not problems and len(text) <= SMS_ONE_SEGMENT_TARGET:      # one segment, always (spec/37)
                return text
            if text and not problems:
                problems = [f"{len(text)} characters - shorten it to {SMS_ONE_SEGMENT_TARGET} or fewer "
                            "including the opt-out line (one SMS segment)"]
            logger.warning("generate_vm_sms: draft rejected (%s) | contact_id=%s attempt=%d",
                           "; ".join(problems) or "empty", context.contact_id, attempt + 1)
            messages = messages + [
                {"role": "assistant", "content": json.dumps({"sms_text": text})},
                {"role": "user", "content": "That draft was rejected: " + ("; ".join(problems) or "empty")
                 + ". Rewrite it as a short missed-call notification only, following every hard rule."},
            ]
    except Exception as exc:
        logger.warning("generate_vm_sms: AI failed, using fallback | contact_id=%s: %s", context.contact_id, exc)
    if not brand:
        brand = {"brand_name": "Colaberry", "offer_name": "AI Systems Architect Accelerator",
                 "unsubscribe_text": "Text STOP to stop alerts"}
    return _sms_notice_fallback(context, brand)


def generate_vm_email(
    context: ConversationContext, settings: Any = None, session: Any = None, *, _client: Any = None,
) -> dict[str, str]:
    """The follow-up email: describes the one program on offer (spec/37). Always returns usable content."""
    from app.core import offer

    try:
        from app.adapters.openai_client import OpenAIClient
        from app.prompts.families import vm_followup_generator  # noqa: F401 - register prompts
        from app.prompts.registry import get_prompt

        brand = _load_brand_context(session, settings)
        terms = _offer_terms(session, settings)
        version = _ATTEMPT_TO_PROMPT_VERSION.get(context.attempt_number, "tier_final")
        entry = get_prompt("vm_followup_generator", version)
        messages = entry.build_messages(
            lead_first_name=context.lead_first_name or "there",
            campaign_name="AI training",
            prior_messages=_format_prior_messages(context.outbound_messages),
            ghl_conversation_thread=_format_ghl_thread(context.ghl_messages),
            **brand,
        )
        messages[0]["content"] = messages[0]["content"].format(
            brand_name=brand["brand_name"], offer_name=brand["offer_name"], offer_facts=brand["offer_facts"])
        client = OpenAIClient(settings=settings, _client=_client)
        for attempt in range(2):
            result = client.chat_completion(
                messages=messages, model=_get_model(settings),
                response_format={"type": "json_object"}, _retry_delay=0.0)
            out = {
                "email_subject": str(result.get("email_subject", "")).strip(),
                "preview_text": str(result.get("preview_text", "")).strip(),
                "email_html": str(result.get("email_html", "")).strip(),
                "email_text": str(result.get("email_text", "")).strip(),
            }
            out["email_text"] = out["email_text"] or out["email_html"]
            hits = offer.forbidden_hits(" ".join(out.values()), terms)
            if out["email_subject"] and out["email_html"] and not hits:
                return out
            why = ("names a course we no longer offer: " + ", ".join(hits)) if hits else "incomplete"
            logger.warning("generate_vm_email: draft rejected (%s) | contact_id=%s attempt=%d",
                           why, context.contact_id, attempt + 1)
            messages = messages + [
                {"role": "assistant", "content": json.dumps(out)},
                {"role": "user", "content": f"That draft was rejected: {why}. Rewrite it, offering only the "
                                            f"{brand['offer_name']} and following every hard rule."},
            ]
    except Exception as exc:
        logger.warning("generate_vm_email: AI failed, using fallback | contact_id=%s: %s", context.contact_id, exc)
    fb = get_email_fallback()
    return {
        "email_subject": fb.subject, "preview_text": "",
        "email_html": f"<p>{fb.body.replace(chr(10), '</p><p>')}</p>", "email_text": fb.body,
    }


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
        support_ghl_id = _load_support_ghl_id(session, settings)
        ipbc_ghl_id = _load_ipbc_ghl_id(session, settings)
        messages[0]["content"] = (
            messages[0]["content"]
            .replace("ADMISSIONS_GHL_ID_HERE", admissions_ghl_id)
            .replace("SUPPORT_GHL_ID_HERE", support_ghl_id)
            .replace("IPBC_GHL_ID_HERE", ipbc_ghl_id)
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


_SUPPORT_FALLBACK_GHL_ID = "yIhCTptvoNLixaWkLcRd"  # Balakrishna
_IPBC_FALLBACK_GHL_ID = "93bhNRgb5pzSoHmaSimH"  # Taiwo


def _load_support_ghl_id(session: Any, settings: Any) -> str:
    """
    Return the GHL user ID of whoever is on shift right now for support calls.

    Reads the 'support_staff_roster' key from app_config (JSON list of
    {"name", "ghl_id", "days", "shift_start", "shift_end"}) and resolves the
    on-shift assignee via resolve_shift_assignee() — see app/core/staff_roster.py
    for the overlap/gap rules. Falls back to the hardcoded default when the
    key is absent, unreadable, or no roster entry covers the current time.
    """
    import json
    from datetime import datetime, timezone

    from app.core.staff_roster import resolve_shift_assignee

    raw = None
    try:
        if session is not None:
            from app.core.app_config import get_str
            raw = get_str("support_staff_roster", session, settings, "")
        elif settings is not None:
            raw = str(getattr(settings, "support_staff_roster", None) or "")
    except Exception:
        logger.warning("_load_support_ghl_id: failed to read config — using fallback")

    if raw:
        try:
            roster = json.loads(raw)
            if roster and isinstance(roster, list):
                tz_name = getattr(settings, "default_timezone", None) or "America/Chicago"
                ghl_id = resolve_shift_assignee(roster, datetime.now(timezone.utc), tz_name)
                if ghl_id:
                    return ghl_id
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            logger.warning("_load_support_ghl_id: malformed JSON — using fallback")

    return _SUPPORT_FALLBACK_GHL_ID


def _load_ipbc_ghl_id(session: Any, settings: Any) -> str:
    """
    Return the GHL user ID for the primary IPBC/payment assignee.

    Reads the 'ipbc_payment_assistants' key from app_config (JSON list of
    {"name": str, "ghl_id": str}). The first entry is the active assignee.
    Falls back to the hardcoded default when the key is absent or unreadable.
    """
    import json

    raw = None
    try:
        if session is not None:
            from app.core.app_config import get_str
            raw = get_str("ipbc_payment_assistants", session, settings, "")
        elif settings is not None:
            raw = str(getattr(settings, "ipbc_payment_assistants", None) or "")
    except Exception:
        logger.warning("_load_ipbc_ghl_id: failed to read config — using fallback")

    if raw:
        try:
            assistants = json.loads(raw)
            if assistants and isinstance(assistants, list):
                ghl_id = assistants[0].get("ghl_id", "").strip()
                if ghl_id:
                    return ghl_id
        except (json.JSONDecodeError, AttributeError, IndexError):
            logger.warning("_load_ipbc_ghl_id: malformed JSON — using fallback")

    return _IPBC_FALLBACK_GHL_ID


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

    from app.core import offer
    from app.core.schedule_context import (
        DEFAULT_FREE_SIGNUP_URL,
        NONE_SCHEDULED,
        build_schedule_block,
        is_unset,
    )

    class_start = _gs("next_class_start", "")
    open_house = _gs("next_open_house_date", "")
    rsvp = _gs("live_open_house_link", "")
    free_url = _gs("free_signup_url", DEFAULT_FREE_SIGNUP_URL) or DEFAULT_FREE_SIGNUP_URL
    oh_active = not is_unset(open_house)

    return {
        "schedule_block":             build_schedule_block(class_start, open_house, rsvp, free_url),
        "free_signup_url":            free_url,
        "brand_name":                 _gs("brand_name", "Colaberry"),
        "sender_name":                _gs("sender_name", "Cora from Colaberry"),
        "reply_to_email":             _gs("reply_to_email", "admissions@colaberry.com"),
        "next_class_start":           NONE_SCHEDULED if is_unset(class_start) else class_start,
        "next_open_house_date":       NONE_SCHEDULED if not oh_active else open_house,
        # An RSVP link for an Open House that is no longer scheduled must never be sent.
        "live_open_house_link":       rsvp if oh_active else "",
        "explainer_video_link":       _gs("explainer_open_house_video_link", ""),
        "unsubscribe_text":           _gs("unsubscribe_text", "Text STOP to stop alerts"),
        "offer_name":                 _gs("offer_name", offer.DEFAULT_OFFER_NAME) or offer.DEFAULT_OFFER_NAME,
        "offer_facts":                (_gs("offer_facts", offer.DEFAULT_OFFER_FACTS) or offer.DEFAULT_OFFER_FACTS)
                                      .replace("{free_url}", free_url),
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
