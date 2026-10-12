"""
LLM judge for the GHL content audit (spec/35).

The keyword audit flags any outbound text that names a retired course. That includes a correct refusal ("we are no
longer enrolling in the Data Analytics bootcamp"). This judge reads the flagged message (and the lead's message it
answers) and says whether it only DECLINES the retired course or OFFERS / promotes it.

Safety: the judge can only REMOVE a flag. Anything but a clear "declines" - "offers", "unclear", a timeout, an API error,
malformed JSON, an exhausted budget - leaves the message flagged exactly as before. Employment / placement claims are never
sent here (content_audit.course_only).
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

DECLINES, OFFERS, UNCLEAR = "declines", "offers", "unclear"
_CACHE_MAX = 500
_cache: "OrderedDict[str, str]" = OrderedDict()      # message id -> verdict; a re-walked message is not judged twice

_PROMPT = (
    "You audit one outbound admissions message. Colaberry has RETIRED some courses and no longer sells them. "
    "Given the lead's previous message (may be empty) and our flagged reply, decide what our reply does with the retired "
    "course. Reply JSON: {\"verdict\": \"declines\"|\"offers\"|\"unclear\", \"reason\": short}. "
    "'declines' = the reply ONLY says the retired course is no longer available / no longer enrolling (optionally pointing "
    "to the current program) and does NOT promote, describe, price, schedule or invite enrollment in the retired course. "
    "'offers' = the reply promotes, describes, prices, schedules, confirms availability of, or invites enrollment in the "
    "retired course, or mentions it in any other way. 'unclear' = anything else. Be conservative: when unsure, 'unclear'.\n"
    "Examples:\n"
    "- 'We no longer enroll new students in the Data Analytics bootcamp. We now offer the AI Systems Architect Accelerator.' "
    "-> declines\n"
    "- 'Our Data Analytics bootcamp starts Monday, want a spot?' -> offers\n"
    "- 'We no longer run the bootcamp, but the Data Analytics bootcamp has a few seats left.' -> offers\n"
    "- 'Yes, the Tableau course is still available.' -> offers"
)


def judge(settings: Any, message_id: str, body: str, prior: str | None) -> str:
    """Verdict for one flagged message. Never raises; any failure is UNCLEAR (= stay flagged)."""
    if message_id in _cache:
        return _cache[message_id]
    try:
        from app.adapters.openai_client import OpenAIClient

        out = OpenAIClient(settings=settings).chat_completion(
            [{"role": "system", "content": _PROMPT},
             {"role": "user", "content": f"Lead's previous message: {(prior or '(none)')[:400]}\nOur reply: {body[:800]}"}],
            model=getattr(settings, "openai_model_content_audit", "gpt-4o-mini"),
            response_format={"type": "json_object"}, max_tokens=100)
        verdict = str(out.get("verdict", UNCLEAR)).strip().lower()
        if verdict not in (DECLINES, OFFERS, UNCLEAR):
            verdict = UNCLEAR
    except Exception as exc:
        logger.warning("content_judge: LLM unavailable, message stays flagged: %s", exc)
        return UNCLEAR                                # not cached: try again on the next run
    _cache[message_id] = verdict
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return verdict
