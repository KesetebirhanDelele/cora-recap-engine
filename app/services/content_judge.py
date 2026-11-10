"""
LLM judge for the GHL content audit (spec/35).

The keyword audit flags any outbound text or email that names a retired course. Most of those are not offers: a correct refusal
("we no longer enroll in the Data Analytics bootcamp"), a staff text explaining the move to the AI program, a call recap quoting
what a caller said, an interview notice naming a job title. Keywords cannot tell these apart from a real offer, so this judge
reads the whole message in context and answers one question: does it OFFER a retired course?

Since 2026-11-09 (Kes) the same judge serves all three word lists: retired courses, employment / placement claims, and the
word "free". Each flagged category brings its own rule (the _RULE_* blocks below); the message is cleared only when none of the
rules is broken. Soft wording such as "prepares you for AI roles" is not an employment claim; a promise, guarantee, rate or
implied job result is.

Safety (fail closed): the judge can only REMOVE a flag, and only on NOT_OFFERING (= no rule broken). "offers", "unclear", a split
between the votes, a timeout, an API error, malformed JSON or an exhausted budget leave the message flagged exactly as before.
There is deliberately no wording list in code: the meaning is decided by the model, and every message it clears is recorded
(channel_events kind 'content_ok') with both results so a person can spot-check what was let through. last_detail() gives the
votes and reason for the alert line.
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
_detail: "OrderedDict[str, dict]" = OrderedDict()    # message id -> {"verdict", "votes", "reason"} for the alert / audit line

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

_FRAME = (
    "You audit ONE outbound message sent by Colaberry. A keyword filter flagged it because it contains a term from a watch "
    "list. Decide, from the whole message in context, whether the message BREAKS the rule(s) below for the flagged term(s).\n"
    "Reply with JSON: {\"verdict\": \"offers\"|\"not_offering\"|\"unclear\", \"reason\": \"one short sentence\"}.\n"
    "'offers' = a rule is broken. 'not_offering' = the term appears but no rule is broken. 'unclear' = you cannot tell. If you "
    "are genuinely unsure, answer 'unclear'. Never guess 'not_offering'. Ignore unsubscribe and footer boilerplate. Judge the "
    "meaning, not the keywords.\n"
)
_RULE_COURSE = (
    "RULE retired course: Colaberry has RETIRED these courses: data analytics / data analyst, data science, bootcamp, Power BI, "
    "Tableau, SQL, Excel, full stack, cybersecurity, business intelligence. Broken = the message OFFERS one (a reader could "
    "believe they can sign up for, enroll in, buy, attend or receive it, or that Colaberry teaches or covers that subject). "
    "NOT broken = it refuses, says it is retired or replaced, explains the move to the AI program, or only quotes what a "
    "person said."
)
_RULE_EMPLOYMENT = (
    "RULE employment: the message must not state, promise, guarantee or imply that Colaberry gets people jobs, places or hires "
    "them, introduces them to employers for a job, or achieves any placement / hiring / employment rate, salary or income "
    "result. Broken = any such statement or implication, including a rate or a success story about jobs obtained.\n"
    "NOT broken: it only DENIES or disclaims such results ('Colaberry does not guarantee job placement', 'we cannot promise "
    "a job'); it describes skills, learning or preparation for AI roles or careers WITHOUT stating or implying a result "
    "('the certification helps you prepare for AI roles', 'build skills for AI work'); it only repeats what the lead said or "
    "asked. A disclaimer that sits beside a real claim in the same message is still broken.\n"
    "Examples:\n"
    "- 'Colaberry does not guarantee job placement.' -> not_offering\n"
    "- 'The certification helps you prepare for AI roles, but we do not guarantee jobs.' -> not_offering\n"
    "- 'You asked about job placement: we cannot promise outcomes.' -> not_offering\n"
    "- 'Our graduates get placed within 90 days, and our placement rate is 71%.' -> offers\n"
    "- 'Finish the program and we will help you land a job.' -> offers\n"
    "- 'We do not guarantee placement, but 9 in 10 of our grads are hired.' -> offers"
)
_RULE_FREE = (
    "RULE free: the message must not describe any Colaberry program, course, plan, class, trial, preview, access or price as "
    "free (including 'free of charge', 'for free', 'start free', 'free AI resources'). Broken = the word describes what "
    "Colaberry offers or charges. NOT broken: the word is unrelated to Colaberry's offer or price ('feel free to reply', "
    "'free time', 'free to call me'), or it appears only inside the approved web address training.colaberry.com/start-free "
    "(the address itself, not a sentence calling the program free).\n"
    "Examples:\n"
    "- 'Feel free to reply any time.' -> not_offering\n"
    "- 'Start at training.colaberry.com/start-free.' -> not_offering\n"
    "- 'The Explorer plan is free to start.' -> offers\n"
    "- 'Join our free class.' -> offers"
)


def prompt_for(categories: set[str]) -> str:
    """Judge instructions for the flagged word lists. Course-only keeps the original prompt unchanged."""
    cats = categories or {"course"}
    if cats == {"course"}:
        return _PROMPT
    parts = [_FRAME]
    if "course" in cats:
        parts.append(_RULE_COURSE)
    if "employment" in cats:
        parts.append(_RULE_EMPLOYMENT)
    if "free" in cats:
        parts.append(_RULE_FREE)
    return "\n".join(parts)


def last_detail(message_id: str) -> dict | None:
    """Votes and reason of the latest judgement for a message (None when it was never judged in this process)."""
    return _detail.get(message_id)


def judge(settings: Any, message_id: str, body: str, prior: str | None, terms: tuple[str, ...] | list[str] | None = None) -> str:
    """Verdict for one flagged message. Never raises; any failure is UNCLEAR (= stay flagged).

    The judge is asked JUDGE_VOTES times. NOT_OFFERING (the only verdict that removes a flag) needs every vote to say it;
    any 'offers' makes it OFFERS; anything else (a split, 'unclear', one failed ask) is UNCLEAR. `terms` are the banned terms
    the keyword scan found, shown to the model so it knows what to look at."""
    if message_id in _cache:
        return _cache[message_id]
    try:
        from app.adapters.openai_client import OpenAIClient
        from app.core import content_audit as ca

        prompt = prompt_for(ca.categories(list(terms or [])))
        client = OpenAIClient(settings=settings)
        votes = []
        reasons: list[str] = []
        for _ in range(JUDGE_VOTES):
            out = client.chat_completion(
                [{"role": "system", "content": prompt},
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
            reasons.append(str(out.get("reason", ""))[:200])
    except Exception as exc:
        logger.warning("content_judge: LLM unavailable, message stays flagged: %s", exc)
        return UNCLEAR                                # not cached: try again on the next run
    verdict = NOT_OFFERING if all(v == NOT_OFFERING for v in votes) else OFFERS if OFFERS in votes else UNCLEAR
    _cache[message_id] = verdict
    _detail[message_id] = {"verdict": verdict, "votes": votes, "reason": next((r for r in reasons if r), "")}
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    while len(_detail) > _CACHE_MAX:
        _detail.popitem(last=False)
    return verdict
