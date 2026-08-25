"""
Conversation-type classification for staff_call_quality (spec/23).

Determines whether a human-staff GHL call was with a lead (→ sales rubric)
or a student (→ support rubric), using a priority chain of signals:

1. GHL contact tags — per Kes (2026-08-25), the most reliably populated
   signal seen in practice (every real contact checked had tags; none had
   the picklist fields below populated). Rule, verbatim from Kes: any tag
   containing "student" or "enrolled" means the contact is a student; if
   tags exist but none do, that itself means lead — Colaberry tags leads
   with campaign labels ("warm lead", "cold lead", "new lead", "email
   lead", "ai lead", etc.) but only explicitly tags the *exception*
   (enrolled students), so absence of a student/enrolled tag is itself a
   signal, not just a non-signal. An empty tag list is different from "has
   tags but none say student" — it falls through to the next signal rather
   than defaulting to "sales" on zero information.
2. The three near-duplicate GHL picklist fields (who_you_are,
   select_option_1, select_option_2), all sharing the same options
   ("Potential Student" | "Current Student" | "Business or Partner") —
   likely leftover from different form builds. Which one is actually
   populated in practice is unknown as of 2026-08-25 (in every contact
   checked live, none were), so all three are checked and the raw values
   are persisted on every row (see StaffCallQuality) so this can be
   revisited once real data comes in — do not consolidate to one field yet.
3. enrollment_date.
4. Cora's own call history (an 'enrolled' intent already detected on a
   prior Cora-placed call).
5. (Handled by the caller, not this function) an AI read of the transcript
   itself — classify_from_transcript_ai(), last resort.

"other" (Business or Partner) is out of scope for both rubrics — calls
classified "other" are recorded but not rubric-scored.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PICKLIST_TO_TYPE = {
    "Current Student": "support",
    "Potential Student": "sales",
    "Business or Partner": "other",
}

# The two "Select an option that best describes you" fields share the exact
# same display name in GHL's UI (leftover duplicate form fields) but distinct
# fieldKeys — must match by fieldKey, not name, to tell them apart. "Who you
# are" and "Enrollment Date" have unique names, so name-matching is fine there.
_WHO_YOU_ARE_FIELD = "Who you are"
_SELECT_OPTION_1_FIELD_KEY = "contact.select_an_option_that_best_describes_you"
_SELECT_OPTION_2_FIELD_KEY = "contact.select_an_option_that_best_describes_you2"
_ENROLLMENT_DATE_FIELD = "Enrollment Date"

# Substrings checked case-insensitively against each tag. Free text, not a
# controlled vocabulary (per Kes, 2026-08-25) — these are the only two
# keywords the rule needs, since "no student/enrolled tag" itself means
# lead (see module docstring). Extend this list only after seeing a real
# tag that should mean "student" but doesn't contain either substring.
_STUDENT_TAG_KEYWORDS = ("student", "enrolled")


def extract_classification_signals(contact: dict) -> dict[str, Any]:
    """
    Pull the raw values classify_from_known_signals() needs from a raw GHL
    contact record (GHLClient.get_contact() output).

    Returns {"tags", "who_you_are_value", "select_option_1_value",
    "select_option_2_value", "enrollment_date"} — never raises; unparseable
    or missing values come back as None (empty list for tags).
    """
    from app.adapters.ghl import GHLClient

    def _scalar(value: Any) -> Optional[str]:
        if isinstance(value, list):
            value = value[0] if value else None
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    tags = [str(t).strip() for t in (contact.get("tags") or []) if str(t).strip()]

    who_you_are = _scalar(GHLClient.get_field_value(_WHO_YOU_ARE_FIELD, contact))
    select_1 = _scalar(GHLClient.get_field_value(_SELECT_OPTION_1_FIELD_KEY, contact))
    select_2 = _scalar(GHLClient.get_field_value(_SELECT_OPTION_2_FIELD_KEY, contact))

    enrollment_date: Optional[datetime] = None
    raw_enrollment = GHLClient.get_field_value(_ENROLLMENT_DATE_FIELD, contact)
    if raw_enrollment:
        try:
            enrollment_date = datetime.fromisoformat(str(raw_enrollment).replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "extract_classification_signals: could not parse enrollment_date=%r", raw_enrollment
            )

    return {
        "tags": tags,
        "who_you_are_value": who_you_are,
        "select_option_1_value": select_1,
        "select_option_2_value": select_2,
        "enrollment_date": enrollment_date,
    }


def classify_from_known_signals(
    *,
    tags: Optional[list[str]] = None,
    who_you_are_value: Optional[str] = None,
    select_option_1_value: Optional[str] = None,
    select_option_2_value: Optional[str] = None,
    enrollment_date: Optional[datetime] = None,
    has_enrolled_call_history: bool = False,
) -> tuple[str, str]:
    """
    Classify using GHL tags → GHL picklist fields → enrollment_date →
    Cora's own call history.

    Returns (conversation_type, source). conversation_type is one of
    "sales" | "support" | "other" | "unknown". "unknown" means none of these
    signals resolved it — callers should fall back to
    classify_from_transcript_ai() before giving up.
    """
    if tags:
        lowered = [t.lower() for t in tags]
        if any(keyword in t for t in lowered for keyword in _STUDENT_TAG_KEYWORDS):
            return "support", "ghl_tags"
        return "sales", "ghl_tags"

    for value, source in (
        (who_you_are_value, "ghl_who_you_are"),
        (select_option_1_value, "ghl_select_option_1"),
        (select_option_2_value, "ghl_select_option_2"),
    ):
        mapped = _PICKLIST_TO_TYPE.get((value or "").strip())
        if mapped:
            return mapped, source

    if enrollment_date is not None:
        return "support", "ghl_enrollment_date"

    if has_enrolled_call_history:
        return "support", "call_history"

    return "unknown", "unknown"


_AI_CLASSIFICATION_PROMPT = """\
You are classifying a phone call transcript between a Colaberry staff member \
and a caller, to determine which of these the caller is:

- "sales": a prospective lead who has not yet enrolled in a Colaberry program \
(the call is admissions/sales-oriented — discussing the program, pricing, \
enrollment, whether to sign up)
- "support": a current, already-enrolled student (the call is about an \
existing issue — coursework, billing on an active account, technical \
problems, schedule questions for classes they're already in)
- "other": neither of the above (e.g. a business partner, vendor, or \
unrelated call)
- "unknown": the transcript does not give enough information to tell

Respond with a JSON object: {{"conversation_type": "sales"|"support"|"other"|"unknown", \
"reasoning": "<one short sentence>"}}

Transcript:
{transcript}
"""


def classify_from_transcript_ai(transcript: str, client) -> tuple[str, str]:
    """
    AI fallback classification when no other signal resolved conversation_type.

    client: an app.adapters.openai_client.OpenAIClient instance (injected so
    callers control settings/model; not constructed here).

    Returns (conversation_type, "ai_inferred"). Falls back to
    ("unknown", "ai_inferred") on any parse/API failure — never raises,
    matching this codebase's pattern for AI calls that shouldn't block a
    pipeline (e.g. app/core/ai_message_generator.py error fallbacks).
    """
    from app.adapters.openai_client import OpenAIError
    from app.config import get_settings

    settings = get_settings()
    try:
        result = client.chat_completion(
            messages=[{
                "role": "user",
                "content": _AI_CLASSIFICATION_PROMPT.format(transcript=transcript[:8000]),
            }],
            model=settings.openai_model_call_analysis,
            response_format={"type": "json_object"},
        )
        conversation_type = result.get("conversation_type")
        if conversation_type in ("sales", "support", "other", "unknown"):
            return conversation_type, "ai_inferred"
        logger.warning(
            "classify_from_transcript_ai: unexpected conversation_type=%r", conversation_type
        )
        return "unknown", "ai_inferred"
    except (OpenAIError, ValueError, KeyError) as exc:
        logger.warning("classify_from_transcript_ai: failed, defaulting to unknown: %s", exc)
        return "unknown", "ai_inferred"
