"""
What Colaberry currently offers - pure, no I/O (spec/37).

Decision (Kes, 2026-10-02): the ONLY course on offer is the AI Systems Architect Accelerator. Outgoing text and
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
    "About 4 hours of live, recorded sessions per week (Mondays and Thursdays), so it fits around a full-time job. "
    "Hands-on work with Claude Code, the Claude API, Model Context Protocol (MCP), Docker and GitHub, ending in a "
    "capstone presented at a live Expo. Preparation for the Anthropic Architect Certification (CCA-F) is built in. "
    "Anyone can start free at {free_url}."
)

# Course / subject names of the retired offer. A text or email containing one is rejected.
DEFAULT_FORBIDDEN_TERMS = (
    "data analytics,data analyst,data science,bootcamp,boot camp,power bi,tableau,sql,excel,"
    "full stack,full-stack,cybersecurity,cyber security,business intelligence"
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


def forbidden_hits(text: str | None, terms: tuple[str, ...]) -> list[str]:
    """Forbidden course terms present in `text` (whole words / phrases, case-insensitive)."""
    t = (text or "").lower()
    hits = []
    for term in terms:
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", t):
            hits.append(term)
    return hits


def marketing_hit(text: str | None) -> str | None:
    m = MARKETING_SMS.search(text or "")
    return m.group(0) if m else None
