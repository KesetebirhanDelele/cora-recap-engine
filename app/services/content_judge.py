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
import re
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


_DECLINE_PHRASE = re.compile(
    r"(?i)\b(?:no longer|discontinu\w+|retir\w+|stopped (?:enrolling|offering|running)|"
    r"not (?:currently |presently )?(?:be(?:ing)? )?(?:enrolling|offering|offered|available|accepting|running)|"
    r"(?:isn't|is not|aren't|are not|won't be|will not be) (?:being )?(?:offered|available|enrolling|running))\b")


def has_decline_phrase(text: str | None) -> bool:
    """True when the text itself says a course is no longer offered. The model's 'declines' only counts if this also holds, so
    a message with no such wording (e.g. an interview notice that merely names a job title) can never be cleared by the LLM."""
    return bool(_DECLINE_PHRASE.search(text or ""))


JUDGE_VOTES = 2         # independent asks per message; the LLM is not deterministic (measured 4 declines / 4 unclear in 8 runs)


def judge(settings: Any, message_id: str, body: str, prior: str | None) -> str:
    """Verdict for one flagged message. Never raises; any failure is UNCLEAR (= stay flagged).

    The judge is asked JUDGE_VOTES times. DECLINES (the only verdict that removes a flag) needs every vote to say declines;
    any 'offers' makes it OFFERS; anything else (a split, 'unclear', one failed ask) is UNCLEAR."""
    if message_id in _cache:
        return _cache[message_id]
    try:
        from app.adapters.openai_client import OpenAIClient

        client = OpenAIClient(settings=settings)
        votes = []
        for _ in range(JUDGE_VOTES):
            out = client.chat_completion(
                [{"role": "system", "content": _PROMPT},
                 {"role": "user", "content": f"Lead's previous message: {(prior or '(none)')[:400]}\nOur reply: {body[:800]}"}],
                model=getattr(settings, "openai_model_content_audit", "gpt-4o-mini"),
                response_format={"type": "json_object"}, max_tokens=100)
            vote = str(out.get("verdict", UNCLEAR)).strip().lower()
            votes.append(vote if vote in (DECLINES, OFFERS, UNCLEAR) else UNCLEAR)
    except Exception as exc:
        logger.warning("content_judge: LLM unavailable, message stays flagged: %s", exc)
        return UNCLEAR                                # not cached: try again on the next run
    verdict = DECLINES if all(v == DECLINES for v in votes) else OFFERS if OFFERS in votes else UNCLEAR
    if verdict == DECLINES and not has_decline_phrase(body):
        verdict = UNCLEAR                             # the model says decline but the text never says it: stay flagged
    _cache[message_id] = verdict
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return verdict
