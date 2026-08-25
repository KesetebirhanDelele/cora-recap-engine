"""
Extract support-ticket context from a raw GHL contact record (spec/23).

Feeds app.core.call_quality_scoring's support rubric — supporting context
only, not the primary source of "what did the student ask for" (the
transcript is, since it's specific to the exact call being scored; these
GHL fields can be stale or reference an earlier ticket).

Field names below are the exact live field names on this GHL location as of
2026-08-25 (confirmed via GHLClient.get_location_fields()) — Colaberry
tracks up to 4 sequential support tickets per contact via numbered
duplicate fields (no native linked ticketing object), with inconsistent
capitalization ("Support Issue Ticket #1" vs "Support issue Ticket #3") —
that inconsistency is real GHL data, not a typo here.
"""
from __future__ import annotations

from typing import Any

_ISSUE_CATEGORY_FIELDS = [
    "What is your issue related to?",
    "What is your issue related to? Ticket #1",
    "What is your issue related to? Ticket #2",
    "What is your issue related to? Ticket #3",
    "What is your issue related to? Ticket #4",
]

_ISSUE_DESCRIPTION_FIELDS = [
    "Support Issue",
    "Support Issue Ticket #1",
    "Support Issue Ticket #2",
    "Support issue Ticket #3",
    "Support issue Ticket #4",
]

# Student's own post-call CSAT-style survey responses — real ground truth
# when present, but location-level fields, not necessarily tied to the
# specific call being scored.
_SATISFACTION_FIELDS = [
    "1. How satisfied were you with the resolution of your issue?",
    "2. How would you rate the professionalism and friendliness of the support team?",
    "3. How easy was it to get in touch with our support team?",
    "4. Did the support team resolve your issue in a timely manner?",
]


def _as_list(value: Any) -> list[str]:
    """GHL multi-select fields return arrays; single-select return a scalar."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v and str(v).strip()]
    text = str(value).strip()
    return [text] if text else []


def extract_support_context(contact: dict) -> dict[str, Any]:
    """
    Pull issue category/description/satisfaction fields from a raw GHL
    contact record (GHLClient.get_contact() output).

    Returns {"issue_categories": [...], "issue_descriptions": [...],
    "student_satisfaction": {question: answer}} — all keys always present,
    empty list/dict when nothing found (never raises).
    """
    from app.adapters.ghl import GHLClient

    categories: list[str] = []
    for label in _ISSUE_CATEGORY_FIELDS:
        for value in _as_list(GHLClient.get_field_value(label, contact)):
            if value not in categories:
                categories.append(value)

    descriptions: list[str] = []
    for label in _ISSUE_DESCRIPTION_FIELDS:
        value = GHLClient.get_field_value(label, contact)
        if isinstance(value, str) and value.strip():
            descriptions.append(value.strip())

    satisfaction: dict[str, str] = {}
    for label in _SATISFACTION_FIELDS:
        value = GHLClient.get_field_value(label, contact)
        text_values = _as_list(value)
        if text_values:
            satisfaction[label] = ", ".join(text_values)

    return {
        "issue_categories": categories,
        "issue_descriptions": descriptions,
        "student_satisfaction": satisfaction,
    }
