"""
LLM check for the wrong-date monitor (spec/32).

The rule engine in app/core/wrong_date_guard.py decides which event a date belongs to (class start vs Open House) from the
words nearest to it. That guess is wrong whenever one sentence names both events ("an Open House on October 29, where you can
join the next session starting November 12"). Instead of adding more wording rules, this judge reads the whole message and says
whether the flagged dates are really wrong.

Fail closed: only a unanimous "correct" suppresses an alert. "wrong", "unclear", a split between the votes, an error, an
exhausted per-scan budget or `date_llm_enabled=false` all leave the incident open exactly as the rule engine raised it. A
suppressed incident is still recorded (status 'dismissed', resolved_by 'auto: AI date check') so it can be reviewed.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

CORRECT, WRONG, UNCLEAR = "correct", "wrong", "unclear"
VOTES = 2
MAX_BODY_CHARS = 4000
_CACHE_MAX = 500
_cache: "OrderedDict[str, str]" = OrderedDict()

_PROMPT = (
    "You check the dates in ONE outbound message sent by Colaberry. The truth is given below: the date the next class starts and "
    "the date of the next Open House. If a truth value is blank, NOTHING of that kind is scheduled, so any specific date given for "
    "it is wrong. A rule engine flagged some date mentions as possibly wrong, but it only guesses which event a date belongs to "
    "from the nearest words, and it often guesses wrong. Read the whole message, work out what each date really refers to, and "
    "decide.\n"
    "Reply with JSON: {\"verdict\": \"correct\"|\"wrong\"|\"unclear\", \"reason\": \"one short sentence\"}.\n"
    "'correct' = every date in the message that refers to the class start or the Open House matches the truth for THAT event. "
    "A sentence that names both events with their right dates is correct, whatever order the words come in.\n"
    "'wrong' = at least one date is attached to the wrong event (for example the class start date given as the Open House date), "
    "is outdated or different from the truth, or is given for an event that is not scheduled.\n"
    "'unclear' = you cannot tell. If you are genuinely unsure, answer 'unclear'; never guess 'correct'.\n"
    "Dates that are neither the class start nor the Open House (a call-back time, an appointment) are not your concern.\n"
    "Examples (truth: class starts November 12, 2026; Open House October 29, 2026):\n"
    "- 'We have an Open House on October 29, 2026 where you can join the next session starting on November 12, 2026.' -> correct\n"
    "- 'Our next class starts November 12 and the Open House is October 29.' -> correct\n"
    "- 'Join our Open House on November 12, 2026.' -> wrong\n"
    "- 'Classes start October 29.' -> wrong\n"
    "- 'Our next class starts July 16, 2026.' -> wrong\n"
    "- 'We will call you on October 20 to finish up.' -> correct"
)


def review(settings: Any, message_id: str, subject: str | None, body: str, flagged: list[dict],
           expected_class_start: str | None, expected_open_house: str | None) -> str:
    """CORRECT only when every vote says the flagged dates are fine; otherwise WRONG or UNCLEAR. Never raises."""
    if message_id in _cache:
        return _cache[message_id]
    try:
        from app.adapters.openai_client import OpenAIClient

        flagged_txt = "; ".join(
            f"'{f.get('raw')}' (rule engine took it for the {str(f.get('kind', '')).replace('_', ' ')})" for f in flagged) or "(none)"
        user = (
            f"Truth: next class starts: {expected_class_start or '(nothing scheduled)'}; "
            f"next Open House: {expected_open_house or '(nothing scheduled)'}.\n"
            f"Flagged by the rule engine: {flagged_txt}\n"
            f"Subject: {subject or '(none)'}\n"
            f"Message: {body[:MAX_BODY_CHARS]}")
        client = OpenAIClient(settings=settings)
        votes = []
        for _ in range(VOTES):
            out = client.chat_completion(
                [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user}],
                model=getattr(settings, "openai_model_content_audit", "gpt-4o-mini"),
                response_format={"type": "json_object"}, max_tokens=120)
            vote = str(out.get("verdict", UNCLEAR)).strip().lower()
            votes.append(vote if vote in (CORRECT, WRONG, UNCLEAR) else UNCLEAR)
    except Exception as exc:
        logger.warning("date_judge: LLM unavailable, incident stays open: %s", exc)
        return UNCLEAR                                  # not cached: try again next cycle
    verdict = CORRECT if all(v == CORRECT for v in votes) else WRONG if WRONG in votes else UNCLEAR
    _cache[message_id] = verdict
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return verdict
