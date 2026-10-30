"""
What Colaberry currently offers - pure, no I/O (spec/37).

Decision (Kes, 2026-10-01): the ONLY course on offer is the AI Systems Architect Accelerator. Outgoing text and
email must never offer or name any other course. Facts below follow docs/colaberry-knowledge-base.md (the
source of truth for Cora); every value can be overridden in app_config without a deploy:
  offer_name, offer_facts, offer_forbidden_terms

SMS is a NOTIFICATION channel (missed-call notice, upcoming-call reminder), not marketing. Marketing wording is
rejected in a follow-up SMS (MARKETING_SMS); email is where the program is described.
"""
from __future__ import annotations

import re

DEFAULT_OFFER_NAME = "AI Systems Architect Accelerator"

DEFAULT_OFFER_FACTS = (
    "A 12-week online program for working professionals who want to design, build and govern AI-powered systems. "
    "Hands-on work with Claude Code, the Claude API, Model Context Protocol (MCP), Docker and GitHub, ending in a "
    "capstone presented at a live Expo. Preparation for the Anthropic Architect Certification (CCA-F) is built in. "
    "Two ways to take it: SELF-PACED, with enrollment open all the time so you can start whenever you like "
    "(anyone can start for $0 at {free_url} - no card needed; membership from $149/mo, state that price only if the "
    "lead asks about cost); or LIVE CLASSES, about 4 hours a week of instructor-led sessions on "
    "Mondays and Thursdays (all recorded) - the Open House is the way to learn about and join the next live class "
    "(when no Open House is scheduled, say the next Open House date is to be determined and point to the $0 start link). "
    "Never use the word 'free' (Texas Workforce Commission rule for a licensed career school): say '$0 to start' "
    "and 'no card needed' instead."
)

# Course / subject names of the retired offer. A text or email containing one is rejected.
DEFAULT_FORBIDDEN_TERMS = (
    "data analytics,data analyst,data science,bootcamp,boot camp,power bi,tableau,sql,excel,"
    "full stack,full-stack,cybersecurity,cyber security,business intelligence,"
    # Ali 2026-10-04: "free" is not allowed as a standalone claim (TWC advertising rule for a licensed career school).
    # "feel free to ..." and "free time" are not claims and are excepted in forbidden_hits().
    "free,"
    # Never state an employment / placement outcome: an unprovable number to a prospective student is a regulatory exposure
    # (Ali, 2026-10-03: only with his written approval of the number and its source).
    "employment rate,placement rate,job placement,job guarantee,guaranteed job,hiring rate,employment outcome"
)

# Wording that belongs in email, not in a missed-call notification SMS.
MARKETING_SMS = re.compile(
    r"(?i)\b(open\s+house|rsvp|webinar|explainer|enroll\w*|enrol\w*|register\w*|sign\s*up|discount|limited\s+time|"
    r"free\s+class|scholarship|tuition|pric(?:e|es|ing)|success\s+stor\w*|watch\s+(?:their|our|the)|"
    r"class(?:es)?\s+(?:starts?|begins?)|starts?\s+on|cohort|hurry|last\s+chance|spots?\s+(?:left|filling))\b|\$\s?\d"
)


def parse_terms(value: str | None) -> tuple[str, ...]:
    raw = DEFAULT_FORBIDDEN_TERMS if value is None else value
    return tuple(t.strip().lower() for t in raw.split(",") if t.strip())


# Per-term exceptions: (lookbehinds, lookaheads) wrapped around the whole-word match. The text is lower-cased with
# whitespace collapsed to single spaces first, so a literal single space is safe inside a lookbehind.
#   excel: the verb ("skills needed to excel", "you will excel") is not the retired Excel course; "learn Excel",
#          "Microsoft Excel", "Excel course" still hit.
#   free:  "feel free to ask" and "free time" are not advertising claims; "start free", "free account", "free class",
#          "risk-free" still hit.
_VERB_BEFORE_EXCEL = ("to", "will", "can", "could", "would", "may", "might", "should", "must", "who", "you", "we", "they")
_TERM_EXCEPTIONS: dict[str, tuple[str, str]] = {
    "excel": ("".join(rf"(?<!\b{w} )" for w in _VERB_BEFORE_EXCEL), ""),
    "free": (r"(?<!\bfeel )", r"(?! time\b)"),
}


# Colaberry's own $0 sign-up link. Its path contains the word "free" but it is the approved link (Kes 2026-10-20), so it is
# removed before the scan. Only this exact path: "/start-free-trial", "/start-free2" and other hosts still hit.
_ALLOWED_FREE_LINK = re.compile(
    r"(?:https?://)?(?:www\.)?training\.colaberry\.com/start-free(?![a-z0-9-])(?:/|\?[^\s\"'<>]*)?")


def forbidden_hits(text: str | None, terms: tuple[str, ...]) -> list[str]:
    """Forbidden terms present in `text` (whole words / phrases, case-insensitive; see _TERM_EXCEPTIONS and _ALLOWED_FREE_LINK)."""
    t = re.sub(r"\s+", " ", _ALLOWED_FREE_LINK.sub(" ", (text or "").lower()))
    hits = []
    for term in terms:
        pre, post = _TERM_EXCEPTIONS.get(term, ("", ""))
        if re.search(r"(?<![a-z0-9])" + pre + re.escape(term) + r"(?![a-z0-9])" + post, t):
            hits.append(term)
    return hits


def marketing_hit(text: str | None) -> str | None:
    m = MARKETING_SMS.search(text or "")
    return m.group(0) if m else None
