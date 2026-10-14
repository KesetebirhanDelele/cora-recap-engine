"""
LLM judge for the GHL content audit (spec/35).

The keyword audit flags any outbound text or email that names a retired course. Most of those are not offers: a correct refusal
("we no longer enroll in the Data Analytics bootcamp"), a staff text explaining the move to the AI program, a call recap quoting
what a caller said, an interview notice naming a job title. Keywords cannot tell these apart from a real offer, so this judge
reads the whole message in context and answers one question: does it OFFER a retired course?

Safety (fail closed): the judge can only REMOVE a flag, and only on NOT_OFFERING. "offers", "unclear", a split between the
votes, a timeout, an API error, malformed JSON or an exhausted budget leave the message flagged exactly as before. Employment /
placement claims and the word "free" are never sent here (content_audit.course_only). There is deliberately no wording list in
code: the meaning is decided by the model, and every message it clears is recorded (channel_events kind 'content_ok') so a
person can spot-check what was let through.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

NOT_OFFERING, OFFERS, UNCLEAR = "not_offering", "offers", "unclear"
DECLINES = NOT_OFFERING            # old name, kept so existing callers / tests keep working
_CACHE_MAX = 500
_cache: "OrderedDict[str, str]" = OrderedDict()      # message id -> verdict; a re-walked message is not judged twice

JUDGE_VOTES = 2         # independent asks per message; the LLM is not deterministic, so ALL must agree to clear
MAX_BODY_CHARS = 3000   # long marketing emails keep the offer well past the first screen

_PROMPT = (
    "You audit ONE outbound message sent by Colaberry. Colaberry has RETIRED these courses and no longer sells them: data "
    "analytics / data analyst, data science, bootcamp, Power BI, Tableau, SQL, Excel, full stack, cybersecurity, business "
    "intelligence. The message was flagged only because it contains one of those terms. Decide, from the whole message in "
    "context, whether it OFFERS a retired course.\n"
    "Reply with JSON: {\"verdict\": \"offers\"|\"not_offering\"|\"unclear\", \"reason\": \"one short sentence\"}.\n"
    "'offers' = a reader could come away believing they can sign up for, enroll in, buy, attend or receive a retired course or "
    "its training from Colaberry. That includes promoting it, describing it as available, pricing or scheduling it, inviting "
    "enrollment, a call or a registration about it, and saying Colaberry teaches or covers that subject (even inside a list of "
    "what the current program teaches).\n"
    "'not_offering' = the term appears but nothing retired is offered. Examples: the message says the course is retired, "
    "replaced or no longer available, or explains the move to the current AI program; it only quotes, summarizes or reports "
    "what a person said; the term is a job title, a skill the person already has, or a topic the person asked about, with no "
    "offer from Colaberry; it contrasts the current program with the old one without offering the old one.\n"
    "'unclear' = you cannot tell. If you are genuinely unsure, answer 'unclear'. Never guess 'not_offering'.\n"
    "Ignore unsubscribe and footer boilerplate. Judge the meaning, not the keywords.\n"
    "Examples:\n"
    "- 'We no longer enroll new students in the Data Analytics bootcamp. We now offer the AI Systems Architect Accelerator.' "
    "-> not_offering\n"
    "- 'Colaberry has transitioned from a standalone Data Analytics program to AI training.' -> not_offering\n"
    "- 'Rather than focusing on Data Analytics alone, the new program teaches you to build AI systems.' -> not_offering\n"
    "- 'Your Data Analyst interview with Acme is Thursday at 2 PM.' -> not_offering\n"
    "- 'The caller, an alumnus, asked how to present his experience for a senior Power BI developer role.' -> not_offering\n"
    "- 'Our Data Analytics bootcamp starts Monday, want a spot?' -> offers\n"
    "- 'We no longer run the bootcamp, but the Data Analytics bootcamp has a few seats left.' -> offers\n"
    "- 'Yes, the Tableau course is still available.' -> offers\n"
    "- 'Join our Open House to explore Data Analytics, Excel and SQL training and explore financing options.' -> offers\n"
    "- 'The program covers Python, SQL and Power BI.' -> offers"
)


def judge(settings: Any, message_id: str, body: str, prior: str | None, terms: tuple[str, ...] | list[str] | None = None) -> str:
    """Verdict for one flagged message. Never raises; any failure is UNCLEAR (= stay flagged).

    The judge is asked JUDGE_VOTES times. NOT_OFFERING (the only verdict that removes a flag) needs every vote to say it;
    any 'offers' makes it OFFERS; anything else (a split, 'unclear', one failed ask) is UNCLEAR. `terms` are the banned terms
    the keyword scan found, shown to the model so it knows what to look at."""
    if message_id in _cache:
        return _cache[message_id]
    try:
        from app.adapters.openai_client import OpenAIClient

        client = OpenAIClient(settings=settings)
        votes = []
        for _ in range(JUDGE_VOTES):
            out = client.chat_completion(
                [{"role": "system", "content": _PROMPT},
                 {"role": "user", "content": (
                     f"Flagged terms: {', '.join(terms) if terms else '(a retired course name)'}\n"
                     f"Lead's previous message: {(prior or '(none)')[:400]}\n"
                     f"Our message: {body[:MAX_BODY_CHARS]}")}],
                model=getattr(settings, "openai_model_content_audit", "gpt-4o-mini"),
                response_format={"type": "json_object"}, max_tokens=120)
            vote = str(out.get("verdict", UNCLEAR)).strip().lower()
            if vote == "declines":                    # the previous prompt's word for the same thing
                vote = NOT_OFFERING
            votes.append(vote if vote in (NOT_OFFERING, OFFERS, UNCLEAR) else UNCLEAR)
    except Exception as exc:
        logger.warning("content_judge: LLM unavailable, message stays flagged: %s", exc)
        return UNCLEAR                                # not cached: try again on the next run
    verdict = NOT_OFFERING if all(v == NOT_OFFERING for v in votes) else OFFERS if OFFERS in votes else UNCLEAR
    _cache[message_id] = verdict
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return verdict
