"""
Quality-scoring rubrics for staff_call_quality (spec/23).

Two rubrics, not one generic scorer — a sales call and a support call
optimize for different things (see directives/spec/23 for the full
rationale). Support calls are judged against what the student actually
asked for, not a fixed checklist: the prompt has the model first identify
the caller's stated request from the transcript itself (the most reliable
source — it's specific to that exact call), then score resolution against
that specific request. GHL's support-ticket custom fields and the student's
own post-call CSAT survey responses (when available) are passed as
supporting context, not the primary source — they can be stale or from a
different ticket than the one this call was about.

Sales rubric's compliance dimension is sourced from
docs/synthflow-warm-lead-prompt.md and docs/synthflow-cold-lead-prompt.md
§5 "Additional Rules" (identical in both) — not invented policy.

Call-outcome gate: score_call() returns a no-score stub for calls that never
connected (voicemail, busy, no-answer) — there's no conversation to judge.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MIN_CONNECTED_DURATION_SECONDS = 20

_SALES_RUBRIC_PROMPT = """\
You are scoring a phone call between a Colaberry sales/admissions \
representative and a prospective lead for the "AI Systems Architect \
Accelerator" program. Score the representative's performance, not the \
lead's.

Score each dimension 0-20, then give an overall_score 0-100 (sum, or your \
holistic judgment if the parts don't cleanly add up) and a one-paragraph \
summary.

Dimensions:
1. opening_rapport (0-20): Did the rep identify themselves and Colaberry \
clearly, and build rapport before pitching?
2. discovery (0-20): Did the rep ask about the lead's background/goals \
before pitching, rather than reciting a generic script?
3. value_articulation (0-20): Was the program's value framed against what \
*this* lead said they wanted?
4. objection_handling (0-20): Were the lead's concerns acknowledged and \
addressed, not talked over or ignored? (Score 20 if no objections arose.)
5. compliance (0-20): Check specifically for these required-disclosure \
rules (source: Colaberry's own voice-agent scripts, same standard applies \
to human reps):
   - Did the rep avoid implying guaranteed job placement? (must not say \
"you will get a job" — acceptable phrasing is "prepare for" / "position \
you for" opportunities)
   - If pricing came up, was it accurate and complete — both the Colaberry \
program cost (Free Explorer $0; Annual $149/mo billed $1,788/yr; \
Month-to-Month $199/mo) AND the separate Anthropic tooling cost \
(~$20/mo Claude Code + ~$10/mo API usage)?
   - If SMS or an appointment was offered, was the lead's explicit \
permission obtained first?
   - If scholarships came up, was the rep honest that none are currently \
available (not overpromising)?
   - The rep did NOT offer or imply enrollment in the Data Analytics \
bootcamp (closed to new students).
   Deduct points for any violation found; note which ones in compliance_notes.

Also return:
- next_step_confirmed (bool): did the call end with a concrete, confirmed \
action (booking, enrollment, or explicit decline) rather than a vague \
"I'll think about it" with no follow-up plan?
- compliance_notes (string): specific violations found, or "none found"
- flagged_reason (string or null): set only if something in this call \
needs a human's attention (a clear compliance violation, misleading \
claim, or pressure tactic) — otherwise null

Respond as JSON: {{"opening_rapport": int, "discovery": int, \
"value_articulation": int, "objection_handling": int, "compliance": int, \
"overall_score": int, "next_step_confirmed": bool, \
"compliance_notes": string, "flagged_reason": string|null, "summary": string}}

Transcript:
{transcript}
"""

_SUPPORT_RUBRIC_PROMPT = """\
You are scoring a phone call between a Colaberry support staff member and a \
current student. Score the staff member's performance, not the student's.

First, identify what the student actually called about — read the \
transcript and state their specific request/issue in your own words. Score \
resolution and every other dimension against THAT specific request, not a \
generic checklist. Use the supporting context below only to fill gaps if \
the transcript itself is unclear (e.g. a partial recording) — the \
transcript is the primary source, since it's specific to this exact call.

Supporting context (may be stale or from a different, earlier ticket — \
treat as background, not ground truth for this specific call):
{support_context}

Score each dimension 0-20, then an overall_score 0-100 and a one-paragraph \
summary.

Dimensions:
1. acknowledgment (0-20): Did staff register the student's actual issue \
before responding, rather than jumping to a script?
2. accuracy (0-20): Is the information given (deadlines, policies, course \
content) correct, as far as you can tell from context? Weight this \
heavily — wrong info here causes real downstream harm to a student.
3. resolution (0-20): Was the specific issue identified above resolved on \
this call, or given a concrete, owned next step (not "someone will get \
back to you" with no owner or timeline)?
4. escalation_judgment (0-20): If the issue was beyond what this staff \
member could resolve, did they escalate appropriately rather than \
guessing or stalling? (Score 20 if no escalation was needed.)
5. tone (0-20): Patience and empathy, especially if the student was \
frustrated or confused — not defensive or dismissive.

Also return:
- identified_request (string): the student's actual request, in your own words
- resolved (bool): was the identified request actually resolved on this call
- flagged_reason (string or null): set only if something needs a human's \
attention (incorrect information given, a student left without a real \
next step, or clear staff misconduct) — otherwise null

Respond as JSON: {{"identified_request": string, "acknowledgment": int, \
"accuracy": int, "resolution": int, "escalation_judgment": int, "tone": int, \
"overall_score": int, "resolved": bool, "flagged_reason": string|null, \
"summary": string}}

Transcript:
{transcript}
"""


def format_support_context(context: dict[str, Any]) -> str:
    """
    Render extracted GHL support-ticket context into prompt-ready text.

    context: output of app.core.ghl_support_context.extract_support_context().
    Returns "No prior support-ticket context available in GHL." if empty —
    the prompt still works fine without it, since the transcript is primary.
    """
    lines: list[str] = []

    categories = context.get("issue_categories") or []
    if categories:
        lines.append(f"Logged issue categories (may span multiple past tickets): {', '.join(categories)}")

    descriptions = context.get("issue_descriptions") or []
    for i, desc in enumerate(descriptions, 1):
        lines.append(f"Logged issue description {i}: {desc}")

    satisfaction = context.get("student_satisfaction") or {}
    for question, answer in satisfaction.items():
        if answer:
            lines.append(f"Student's own post-call survey — {question}: {answer}")

    return "\n".join(lines) if lines else "No prior support-ticket context available in GHL."


def score_call(
    *,
    transcript: str,
    conversation_type: str,
    duration_seconds: Optional[int],
    call_connected: bool,
    client,
    support_context: Optional[dict[str, Any]] = None,
    model: Optional[str] = None,
) -> Optional[dict]:
    """
    Score a call against the rubric matching conversation_type.

    conversation_type: "sales" | "support" — "other"/"unknown" are not
    scored (caller should skip before calling this).
    call_connected / duration_seconds: the outcome gate — returns None
    (no score) for calls that never had a real conversation to judge.
    client: an OpenAIClient instance (injected).
    support_context: output of extract_support_context(), used only for
    conversation_type == "support".

    Returns the parsed rubric dict (including overall_score, summary,
    flagged_reason), or None if the call shouldn't be scored. Never raises —
    scoring failures return a dict with overall_score=None and the error
    noted in flagged_reason, so a bad AI call doesn't lose the row.
    """
    from app.adapters.openai_client import OpenAIError
    from app.config import get_settings

    if not call_connected or not duration_seconds or duration_seconds < _MIN_CONNECTED_DURATION_SECONDS:
        logger.info(
            "score_call: skipping — not a connected conversation (connected=%s duration=%s)",
            call_connected, duration_seconds,
        )
        return None

    if conversation_type not in ("sales", "support"):
        logger.info("score_call: skipping — conversation_type=%r not scored", conversation_type)
        return None

    settings = get_settings()
    clean_model = model or settings.openai_model_ghl_analysis

    if conversation_type == "sales":
        prompt = _SALES_RUBRIC_PROMPT.format(transcript=transcript[:12000])
    else:
        context_text = format_support_context(support_context or {})
        prompt = _SUPPORT_RUBRIC_PROMPT.format(
            support_context=context_text, transcript=transcript[:12000],
        )

    try:
        result = client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=clean_model,
            response_format={"type": "json_object"},
        )
        return result
    except OpenAIError as exc:
        logger.error("score_call: OpenAI call failed: %s", exc)
        return {
            "overall_score": None,
            "summary": "",
            "flagged_reason": f"Scoring failed: {exc}",
        }
